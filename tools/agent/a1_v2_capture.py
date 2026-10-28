"""Private A1 v2 message evidence; no model, network or storage calls here."""
import copy
import json
from llm import assistant_message
from tools import DATA_TOOLS,all_tool_schemas

class CaptureError(ValueError): pass

def canonical(obj):
    try: return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError) as exc: raise CaptureError('non-JSON evidence') from exc

class CaptureClient:
    def __init__(self, client): self.client=client; self.events=[]
    def chat(self,messages,tools=None,tool_choice='required',max_tokens=None):
        request=copy.deepcopy(messages)
        if tool_choice!='required' or max_tokens is not None: raise CaptureError('request settings differ')
        try: response=self.client.chat(messages,tools=tools,tool_choice=tool_choice,max_tokens=max_tokens)
        except Exception:
            self.events.append({'messages':request,'tools':copy.deepcopy(tools),'tool_choice':tool_choice,'max_tokens':max_tokens,'error':True}); raise
        self.events.append({'messages':request,'tools':copy.deepcopy(tools),'tool_choice':tool_choice,'max_tokens':max_tokens,'response':copy.deepcopy(response)})
        return response

def visible_receipts(result, events):
    if not isinstance(result,dict) or result.get('mode')!='loop' or not isinstance(events,list): raise CaptureError('loop evidence required')
    steps=result.get('steps');trace=result.get('trace')
    from explain import SYSTEM_PROMPT,REPAIR_PROMPT
    from verifier import failure_text
    if events and events[0].get('messages')!=[{'role':'system','content':SYSTEM_PROMPT},{'role':'user','content':'Question: '+str(result.get('question'))}]: raise CaptureError('initial model-visible prompt differs')
    if not isinstance(steps,list) or not isinstance(trace,list): raise CaptureError('steps or trace missing')
    if any(not isinstance(e,dict) or e.get('tool_choice')!='required' or e.get('max_tokens') is not None or e.get('tools')!=all_tool_schemas() for e in events): raise CaptureError('outbound request settings differ')
    successful=[e for e in events if isinstance(e,dict) and 'response' in e]
    failures=[i for i,e in enumerate(events) if e.get('error') is True and 'response' not in e]
    if failures and (failures!=[len(events)-1] or not any(str(x).startswith('llm:') for x in result.get('errors',[]))): raise CaptureError('invalid failed request evidence')
    if len(successful)+len(failures)!=len(events): raise CaptureError('invalid request evidence')
    if len(successful)!=len(steps): raise CaptureError('request count differs')
    receipts=[]; session=[]; ids=set(); prior_messages=None; prior_response=None; data_count=0
    step_index=0
    for event in events:
        messages=event.get('messages');response=event.get('response')
        if not isinstance(messages,list): raise CaptureError('malformed request')
        if response is not None:
            step=steps[step_index];step_index+=1
            if not isinstance(response,dict):raise CaptureError('malformed response')
            calls=response.get('tool_calls')
            if not isinstance(calls,list) or not isinstance(step,dict) or step.get('tool_calls')!=[{k:c.get(k) for k in ('name','arguments','parse_error')} for c in calls]: raise CaptureError('step call evidence differs')
        if prior_messages is not None:
            prefix=prior_messages+[assistant_message(prior_response)]
            if messages[:len(prefix)]!=prefix: raise CaptureError('message history changed or reordered')
            additions=messages[len(prefix):]
            expected=[c for c in prior_response['tool_calls'] if c.get('name')!='submit_explanation']
            if any(not isinstance(m,dict) or m.get('role') not in ('tool','user') for m in additions):raise CaptureError('unexpected message history')
            submit_ids={c.get('id') for c in prior_response['tool_calls'] if c.get('name')=='submit_explanation'}
            if any(m.get('role')=='tool' and m.get('name')=='submit_explanation' and m.get('tool_call_id') not in submit_ids for m in additions):raise CaptureError('unexpected submit reply')
            tool_additions=[m for m in additions if m.get('role')=='tool' and m.get('name')!='submit_explanation']
            if len(expected)!=len(tool_additions): raise CaptureError('missing or duplicate model-visible returns')
            if additions[:len(tool_additions)]!=tool_additions: raise CaptureError('tool return order differs')
            rest=additions[len(tool_additions):]
            if len(rest)>1:raise CaptureError('unexpected added messages')
            if rest:
                extra=rest[0]
                reports=[a.get('report') for a in result.get('attempts',[]) if isinstance(a,dict) and isinstance(a.get('report'),dict) and a['report'].get('pass') is False]
                allowed=[REPAIR_PROMPT.format(failures=failure_text(r)) for r in reports]
                if extra.get('content') not in allowed:raise CaptureError('repair message differs from verifier')
                if extra.get('role')=='user':
                    if prior_response['tool_calls']:raise CaptureError('unexpected user repair')
                elif extra.get('role')=='tool':
                    if extra.get('name')!='submit_explanation' or extra.get('tool_call_id') not in submit_ids:raise CaptureError('unexpected submit repair')
                else:raise CaptureError('unexpected repair role')

            for call,msg in zip(expected,tool_additions):
                cid=call.get('id');name=call.get('name');args=call.get('arguments')
                if not isinstance(cid,str) or not cid or cid in ids or msg.get('tool_call_id')!=cid or msg.get('name')!=name: raise CaptureError('tool return identity mismatch')
                ids.add(cid)
                try: output=json.loads(msg['content'])
                except (KeyError,ValueError,TypeError) as exc: raise CaptureError('malformed tool return') from exc
                if not isinstance(output,(dict,list)) or not isinstance(name,str): raise CaptureError('unsupported tool return')
                receipt={'tool':name,'arguments':args,'output':output}
                receipts.append(receipt)
                if name in DATA_TOOLS and isinstance(args,dict) and call.get('parse_error') is None and data_count<4:
                    session.append({'name':name,'args':args,'output':output});data_count+=1
                elif name in DATA_TOOLS and isinstance(args,dict) and call.get('parse_error') is None:
                    if output!={'error':'tool budget used; call submit_explanation now'}:raise CaptureError('budget reply differs')
                elif output!={'error':call.get('parse_error') or f"unknown tool {name!r}"}:raise CaptureError('unknown or parse-error reply differs')
        prior_messages=messages;prior_response=response
    if result.get('final')!='template' and prior_response and any(c.get('name')!='submit_explanation' for c in prior_response['tool_calls']): raise CaptureError('last calls lack model-visible returns')
    if result.get('final')=='template':
        if canonical(session)!=canonical(trace[:len(session)]): raise CaptureError('session and outbound returns differ')
    elif canonical(session)!=canonical(trace): raise CaptureError('session and outbound returns differ')
    return receipts
