import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifyExchangePage,selectEvidence} from '../client/exchange-tools.mjs';
const originId='bdcf881f-49a6-4898-b286-d7616be0ad5f',topicId='fixture-topic';
function item(seq,content,revision){
  const canonicalJson=JSON.stringify({originId,seq,kind:'knowledge',created:'2026-10-15T00:00:00Z',payload:{id:'fixture-knowledge',topicId,revision,content}});
  return {recordId:originId+':'+seq,sha256:createHash('sha256').update(canonicalJson,'utf8').digest('hex'),canonicalJson};
}
function page(after,next,records){return {format:'hidden-ai-society-exchange-v1',untrusted:true,originId,topicId,after,through:3,records,cursor:{originId,topicId,after:next,through:3},hasMore:next<3,pollAfterSeconds:next<3?1:60};}

test('exact evidence selection preserves revision identity, Unicode and incomplete-snapshot status',()=>{
  const first=page(0,2,[item(2,'old 😀',1)]),last=page(2,3,[item(3,'new e\u0301',2)]);
  const id=last.records[0].recordId;
  assert.equal(selectEvidence([first],[]).scope.complete,false);
  const selected=selectEvidence([first,last],[id]);
  assert.equal(selected.scope.complete,true);
  assert.equal(selected.records[0].record.payload.content,'new e\u0301');
  assert.equal(selected.records[0].record.payload.revision,2);
  assert.equal(selected.records[0].sha256,last.records[0].sha256);
  assert.equal(selectEvidence([last],[id]).scope.complete,false);
});

test('altered bytes, invented IDs, duplicate citations, scope mixing and missing pages are refused',()=>{
  const first=page(0,2,[item(2,'old',1)]),last=page(2,3,[item(3,'new',2)]),id=last.records[0].recordId;
  const changed=structuredClone(last);changed.records[0].canonicalJson=changed.records[0].canonicalJson.replace('new','invented');
  assert.throws(()=>verifyExchangePage(changed));
  assert.throws(()=>selectEvidence([first,last],['nonexistent']));
  assert.throws(()=>selectEvidence([first,last],[id,id]));
  assert.throws(()=>selectEvidence([first,{...last,topicId:'different'}],[id]));
  assert.throws(()=>selectEvidence([first,{...last,after:1}],[id]));
  assert.throws(()=>selectEvidence([first,{...last,through:4}],[id]));
});
