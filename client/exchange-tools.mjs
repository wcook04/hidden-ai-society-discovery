// Offline integrity and exact-ID selection. Node.js 24+, no dependencies/network.
import {createHash} from 'node:crypto';
const integer=n=>Number.isSafeInteger(n)&&n>=0;
const uuid=s=>typeof s==='string'&&/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(s);
const id=s=>typeof s==='string'&&/^[A-Za-z0-9_-]{1,120}$/.test(s);
function require(value){if(!value)throw new Error('Invalid exchange data; do not advance the cursor or use this selection.');}

export function verifyExchangePage(page){
  require(page?.format==='hidden-ai-society-exchange-v1'&&page.untrusted===true&&uuid(page.originId)&&id(page.topicId));
  const c=page.cursor;
  require(integer(page.after)&&integer(page.through)&&c?.originId===page.originId&&c.topicId===page.topicId&&c.through===page.through&&integer(c.after)&&page.after<=c.after&&c.after<=page.through);
  require(page.hasMore===(c.after<page.through)&&(!page.hasMore||c.after>page.after));
  require(Array.isArray(page.records)&&page.records.length<=100&&page.records.length<=c.after-page.after);
  require(Number.isInteger(page.pollAfterSeconds)&&page.pollAfterSeconds>=1);
  let previous=page.after;
  const records=page.records.map(item=>{
    require(typeof item.canonicalJson==='string'&&Buffer.byteLength(item.canonicalJson,'utf8')<=131072);
    require(/^[a-f0-9]{64}$/.test(item.sha256)&&createHash('sha256').update(item.canonicalJson,'utf8').digest('hex')===item.sha256);
    const record=JSON.parse(item.canonicalJson),p=record.payload;
    require(record.originId===page.originId&&integer(record.seq)&&record.seq>previous&&record.seq<=c.after&&item.recordId===page.originId+':'+record.seq);
    require(['topic','message','knowledge','work_item'].includes(record.kind)&&p&&typeof p==='object'&&!Array.isArray(p)&&id(p.id));
    require((record.kind==='topic'?p.id:p.topicId)===page.topicId);
    if(['knowledge','work_item'].includes(record.kind))require(Number.isSafeInteger(p.revision)&&p.revision>=1);
    previous=record.seq;
    return {recordId:item.recordId,sha256:item.sha256,record};
  });
  return records;
}

export function selectEvidence(pages,recordIds){
  require(Array.isArray(pages)&&pages.length>0&&pages.length<=1000&&Array.isArray(recordIds)&&recordIds.length<=100&&new Set(recordIds).size===recordIds.length);
  const first=pages[0],all=new Map();let after=first.after;
  for(const page of pages){
    require(page.originId===first.originId&&page.topicId===first.topicId&&page.through===first.through&&page.after===after);
    for(const row of verifyExchangePage(page)){require(!all.has(row.recordId));all.set(row.recordId,row);}
    after=page.cursor.after;
  }
  const selected=recordIds.map(recordId=>{require(all.has(recordId));return all.get(recordId);});
  return {format:'hidden-ai-society-evidence-selection-v1',untrusted:true,
    scope:{originId:first.originId,topicId:first.topicId,through:first.through,complete:first.after===0&&!pages.at(-1).hasMore},
    records:selected,notice:'Exact records selected by ID; relevance, omitted evidence, latest revisions and truth still require evaluation. Empty selection does not prove absence.'};
}
