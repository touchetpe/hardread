"""HardRead student network: a small set transformer over field, entity and action tokens.

Sequence = [CLS, FIELD, 12 entities, 16 recent public events, A actions] (no positional encoding;
reserve order and legal-menu order do not matter; event recency is a numeric feature). Pre-LayerNorm transformer blocks (fused QKV, GELU-tanh),
then
  policy head  one logit per action token, masked to the legal menu;
  value heads  from CLS: [0] search value (logit of the root value; trains the trunk),
               [1] outcome (logit of P(win)) on stop-gradient CLS features, so the game result,
               which is shared by every decision of a game, cannot make the trunk memorise games;
  opponent head (training-only auxiliary, never an input) from the opponent-active token: logits
               over [move ids | tera move ids | switch to opponent slot 0..5], tied to the move
               embedding table; target = the opponent seat's own search policy at the same
               decision point.
Default size: d=512, 7 layers, 8 heads, FFN 2048 -> ~24.6M parameters.

The forward pass uses only Linear/LayerNorm/Embedding/softmax/GELU-tanh so that infer.py can
mirror it exactly in numpy (no torch needed inside Foul Play).
"""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from encode import (BAG_TABLE, CAT_TABLE, ENT_BAGS, ENT_CAT, EV_CAT, EV_TABLE, F_ACT, F_ENT, F_EV, F_FIELD, N_ENT,
                    N_OWN, TABLE_VOCAB, TOK_ACT, TOK_CLS, TOK_EV, TOK_FIELD, TOK_OPP, TOK_OWN)



class Block(nn.Module):
    def __init__(self, d, heads, ffn, dropout):
        super().__init__()
        self.heads = heads
        self.ln1 = nn.LayerNorm(d)
        self.qkv = nn.Linear(d, 3 * d)
        self.proj = nn.Linear(d, d)
        self.ln2 = nn.LayerNorm(d)
        self.fc1 = nn.Linear(d, ffn)
        self.fc2 = nn.Linear(ffn, d)
        self.drop = nn.Dropout(dropout)
        self.dropout = dropout

    def forward(self, x, attn_mask):
        B, S, D = x.shape
        h = self.ln1(x)
        q, k, v = self.qkv(h).view(B, S, 3, self.heads, D // self.heads).permute(2, 0, 3, 1, 4)
        a = F.scaled_dot_product_attention(q, k, v, attn_mask=attn_mask,
                                           dropout_p=self.dropout if self.training else 0.0)
        x = x + self.drop(self.proj(a.transpose(1, 2).reshape(B, S, D)))
        x = x + self.drop(self.fc2(F.gelu(self.fc1(self.ln2(x)), approximate="tanh")))
        return x


class StudentNet(nn.Module):
    def __init__(self, vocab_sizes, d=512, layers=7, heads=8, ffn=2048, dropout=0.1, events=True, opp_head=True):
        super().__init__()
        self.config = {"vocab_sizes": dict(vocab_sizes), "d": d, "layers": layers, "heads": heads, "ffn": ffn,
                       "dropout": dropout, "events": events, "opp_head": opp_head}
        self.events = events
        self.opp_head = opp_head
        self.emb = nn.ModuleDict({t: nn.Embedding(vocab_sizes[v], d) for t, v in TABLE_VOCAB.items()})
        self.act_kind = nn.Embedding(4, d)
        self.tok_type = nn.Embedding(6, d)
        if events:
            self.ev_num = nn.Linear(F_EV, d)
        self.n_moves = vocab_sizes["moves"]
        if opp_head:  # opponent's next action: [moves | tera moves | switch to opponent slot 0..5]
            self.opp_q = nn.Linear(d, d)
            self.opp_qt = nn.Linear(d, d)
            self.opp_bias = nn.Parameter(torch.zeros(2 * self.n_moves))
            self.opp_sw1 = nn.Linear(d, d // 2)
            self.opp_sw2 = nn.Linear(d // 2, 1)
        self.ent_num = nn.Linear(F_ENT, d)
        self.field_num = nn.Linear(F_FIELD, d)
        self.act_num = nn.Linear(F_ACT, d)
        self.ln_in = nn.LayerNorm(d)
        self.blocks = nn.ModuleList([Block(d, heads, ffn, dropout) for _ in range(layers)])
        self.ln_out = nn.LayerNorm(d)
        self.pol1 = nn.Linear(d, d // 2)
        self.pol2 = nn.Linear(d // 2, 1)
        self.val1 = nn.Linear(d, d // 2)
        self.val2 = nn.Linear(d // 2, 1)
        self.out1 = nn.Linear(d, d // 2)
        self.out2 = nn.Linear(d // 2, 1)
        self.apply(self._init)

    @staticmethod
    def _init(m):
        if isinstance(m, nn.Linear):
            nn.init.normal_(m.weight, std=0.02)
            nn.init.zeros_(m.bias)
        elif isinstance(m, nn.Embedding):
            nn.init.normal_(m.weight, std=0.02)

    def n_params(self):
        return sum(p.numel() for p in self.parameters())

    def forward(self, b):
        """b: dict of tensors from encode.collate (batch first). Returns (policy_logits [B,A], value_logits [B,2])."""
        ent_cat = b["ent_cat"]
        B = ent_cat.shape[0]
        x_ent = self.ent_num(b["ent_num"])
        for j, name in enumerate(ENT_CAT):
            x_ent = x_ent + self.emb[CAT_TABLE[name]](ent_cat[:, :, j])
        for name, _, _ in ENT_BAGS:
            idx = b["ent_bag_" + name]
            e = self.emb[BAG_TABLE[name]](idx) * (idx > 0).unsqueeze(-1).to(x_ent.dtype)
            x_ent = x_ent + e.sum(2) / (idx > 0).sum(2, keepdim=True).clamp(min=1).to(x_ent.dtype)
        own = torch.zeros(N_ENT, dtype=torch.long, device=ent_cat.device)
        own[:N_OWN] = TOK_OWN
        own[N_OWN:] = TOK_OPP
        x_ent = x_ent + self.tok_type(own)

        x_field = (self.field_num(b["field_num"]) + self.emb["weather"](b["field_cat"][:, 0])
                   + self.emb["terrain"](b["field_cat"][:, 1]) + self.tok_type.weight[TOK_FIELD])
        x_cls = self.tok_type.weight[TOK_CLS].expand(B, -1)

        tgt = b["act_target"]
        gathered = torch.gather(x_ent, 1, tgt.clamp(min=0).unsqueeze(-1).expand(-1, -1, x_ent.shape[-1]))
        x_act = (self.act_kind(b["act_kind"]) + self.emb["move"](b["act_move"]) + self.emb["ptype"](b["act_type"])
                 + self.act_num(b["act_num"]) + gathered * (tgt >= 0).unsqueeze(-1).to(x_ent.dtype)
                 + self.tok_type.weight[TOK_ACT])

        toks = [x_cls.unsqueeze(1), x_field.unsqueeze(1), x_ent]
        masks = [torch.ones(B, 2, dtype=torch.bool, device=x_ent.device), b["ent_mask"]]
        if self.events:
            ev_cat = b["ev_cat"]
            x_ev = self.ev_num(b["ev_num"]) + self.tok_type.weight[TOK_EV]
            for j, name in enumerate(EV_CAT):
                x_ev = x_ev + self.emb[EV_TABLE[name]](ev_cat[:, :, j])
            toks.append(x_ev)
            masks.append(b["ev_mask"])
        a0 = sum(t.shape[1] for t in toks)
        x = torch.cat(toks + [x_act], 1)
        x = self.ln_in(x)
        A = x_act.shape[1]
        keep = torch.cat(masks + [b["act_mask"]], 1)
        attn_mask = keep[:, None, None, :]
        for blk in self.blocks:
            x = blk(x, attn_mask)
        x = self.ln_out(x)
        h_act = x[:, a0:a0 + A]
        pol = self.pol2(F.gelu(self.pol1(h_act), approximate="tanh")).squeeze(-1).float()
        pol = pol.masked_fill(~b["act_mask"], -1e9)
        v_search = self.val2(F.gelu(self.val1(x[:, 0]), approximate="tanh"))
        v_out = self.out2(F.gelu(self.out1(x[:, 0].detach()), approximate="tanh"))
        val = torch.cat([v_search, v_out], -1).float()
        if not self.opp_head:
            return pol, val, None
        h_oa = x[:, 2 + N_OWN]  # opponent active entity token
        E_m = self.emb["move"].weight
        opp = torch.cat([self.opp_q(h_oa) @ E_m.t(), self.opp_qt(h_oa) @ E_m.t()], -1) + self.opp_bias
        opp_sw = self.opp_sw2(F.gelu(self.opp_sw1(x[:, 2 + N_OWN:2 + N_ENT]), approximate="tanh")).squeeze(-1)
        opp = torch.cat([opp, opp_sw], -1).float()
        # mask: special move ids, and switches to missing / (outside team preview) active or fainted slots
        tp = b["field_num"][:, 4] > 0.5
        fainted = b["ent_num"][:, N_OWN:, 3] > 0.5
        slot = torch.arange(N_ENT - N_OWN, device=x.device)
        sw_ok = b["ent_mask"][:, N_OWN:] & (tp.unsqueeze(1) | ((slot != 0).unsqueeze(0) & ~fainted))
        mv_ok = torch.ones(2 * self.n_moves, dtype=torch.bool, device=x.device)
        mv_ok[:3] = False
        mv_ok[self.n_moves:self.n_moves + 3] = False
        ok = torch.cat([mv_ok.unsqueeze(0).expand(B, -1), sw_ok], -1)
        return pol, val, opp.masked_fill(~ok, -1e4)


def build(vocab_sizes, size="base", dropout=0.1, events=True, opp_head=True):
    presets = {"base": dict(d=512, layers=7, heads=8, ffn=2048),     # ~24.6M
               "small": dict(d=256, layers=4, heads=4, ffn=1024),    # ~4M
               "tiny": dict(d=128, layers=2, heads=4, ffn=512)}
    return StudentNet(vocab_sizes, dropout=dropout, events=events, opp_head=opp_head, **presets[size])
