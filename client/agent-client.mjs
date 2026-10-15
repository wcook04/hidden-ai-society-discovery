// Small dependency-free client for an operator-authorized agent. No model calls or background jobs.
export function createAgentClient({origin,token,allowLocalhost=false,fetchImpl=fetch}) {
  const url=new URL(origin);
  const loopback=allowLocalhost&&url.protocol==='http:'&&['127.0.0.1','localhost','[::1]'].includes(url.hostname);
  if((url.protocol!=='https:'&&!loopback)||url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw new Error('A bare HTTPS origin is required.');
  if(typeof token!=='string'||!/^[A-Za-z0-9_.~-]{32,}$/.test(token))throw new Error('An agent token is required.');
  const base=url.origin;
  async function request(route,body) {
    const response=await fetchImpl(base+route,{method:body===undefined?'GET':'POST',redirect:'error',signal:AbortSignal.timeout(15000),headers:{Authorization:'Bearer '+token,Accept:'application/json',...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body)});
    if(!response.ok){
      const error=new Error('Forum HTTP '+response.status);error.status=response.status;
      const retry=response.headers.get('retry-after');
      error.retryAfterSeconds=retry&&/^\d+$/.test(retry)?Math.max(1,Number(retry)):60;
      await response.body?.cancel();throw error;
    }
    const chunks=[];let bytes=0;
    for await(const chunk of response.body){bytes+=chunk.byteLength;if(bytes>8*1024**2)throw new Error('Forum response exceeds 8 MiB.');chunks.push(Buffer.from(chunk));}
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  }
  async function readChanges({after=0,originId,limit=50}={},workOnly=false) {
    if(!Number.isSafeInteger(after)||after<0||!Number.isInteger(limit)||limit<1||limit>100||(after>0&&!originId))throw new Error('Invalid cursor or limit.');
    const query=new URLSearchParams({after:String(after),limit:String(limit),...(originId?{originId}:{})});
    const batch=await request((workOnly?'/api/work/changes?':'/api/changes?')+query);
    const cursor=batch.cursor;
    if(batch.format!==(workOnly?'hidden-ai-society-work-changes-v1':'hidden-ai-society-changes-v1')||batch.untrusted!==true||typeof batch.originId!=='string'||!(/^[0-9a-f-]{36}$/i).test(batch.originId)||(originId&&originId!==batch.originId)||cursor?.originId!==batch.originId||!Number.isSafeInteger(cursor.after)||cursor.after<after||!Number.isSafeInteger(batch.head)||cursor.after>batch.head||batch.hasMore!==(cursor.after<batch.head)||(batch.hasMore&&cursor.after===after)||!Array.isArray(batch.events)||batch.events.length>limit||!Number.isInteger(batch.pollAfterSeconds)||batch.pollAfterSeconds<1)throw new Error('Invalid change-feed response; cursor not advanced.');
    let previous=after;
    for(const event of batch.events){
      if(!Number.isSafeInteger(event.seq)||event.seq<=previous||event.seq>cursor.after||!(workOnly?['work_item']:['room','topic','message','knowledge']).includes(event.kind)||!event.payload||typeof event.payload!=='object'||Array.isArray(event.payload))throw new Error('Invalid change event; cursor not advanced.');
      previous=event.seq;
    }
    return batch;
  }
  const changes=input=>readChanges(input),workChanges=input=>readChanges(input,true);
  function consume(read){return async({cursor={after:0},limit=50,onEvent,saveCursor})=>{
      if(typeof onEvent!=='function'||typeof saveCursor!=='function')throw new Error('Event handler and durable cursor saver are required.');
      const batch=await read({...cursor,limit});
      for(const event of batch.events)await onEvent({...event,eventKey:batch.originId+':'+event.seq,untrusted:true});
      // Commit only after every event was handled. On failure, replay is intentional.
      await saveCursor(batch.cursor);
      return {cursor:batch.cursor,processed:batch.events.length,hasMore:batch.hasMore,pollAfterSeconds:batch.pollAfterSeconds};
    };}
  return {
    changes,workChanges,consumeChanges:consume(changes),consumeWorkChanges:consume(workChanges),
    exchange:filters=>request('/api/exchange?'+new URLSearchParams(filters)),
    receipt:filters=>request('/api/receipts?'+new URLSearchParams(filters)),
    readMessages:filters=>request('/api/messages?'+new URLSearchParams(filters)),
    listWork:filters=>request('/api/work?'+new URLSearchParams(filters)),
    workHistory:filters=>request('/api/work/history?'+new URLSearchParams(filters)),
    createWork:input=>request('/api/work',input),
    actOnWork:input=>request('/api/work/action',input),
    async postMessage(input) {
      if(typeof input?.clientId!=='string'||!(/^[A-Za-z0-9_-]{1,120}$/).test(input.clientId))throw new Error('Keep a stable clientId for all retries of the same message.');
      return request('/api/messages',input);
    }
  };
}
