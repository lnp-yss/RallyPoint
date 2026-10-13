const test=require('node:test'), assert=require('node:assert/strict');
const {AnnouncementService,textFor}=require('../pickleball_scoreboard/web/static/announcements.js');
const match={match_id:8,court_id:8,court_name:'Championship Court',teams:{A:[{name:'Dominic'},{name:'Chel'}],B:[{name:'Mike'},{name:'John'}]}};
function harness(storage=new Map()) {
    const spoken=[],logs=[],warnings=[],timers=new Map(); let serial=0;
    const speech={getVoices:()=>[],resume(){},cancel(){},speak(u){spoken.push(u);u.onstart();}};
    const options={speech,Utterance:class{constructor(text){this.text=text;}},storage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},key:'test',uuid:()=>String(++serial),log:x=>logs.push(x),status:x=>warnings.push(x),timer:(f)=>{const id=++serial;timers.set(id,f);return id;},clear:id=>timers.delete(id)};
    const service=new AnnouncementService(options);service.arm();
    return {service,spoken,logs,warnings,timers,options,storage,advance(){const callback=[...timers.values()].at(-1);timers.clear();callback?.();}};
}
test('dynamic courts, doubles and singles template',()=>{
    assert.equal(textFor(match),'Now playing on Championship Court. Dominic and Chel versus Mike and John. Players, please proceed to Championship Court.');
    const singles={...match,teams:{A:[{name:'Alex'}],B:[{name:'Sam'}]}};
    assert.match(textFor(singles),/Alex versus Sam/);
    assert.equal(textFor(singles,'{court}: {team_a_player_1} and {team_a_player_2} versus {team_b_player_1} and {team_b_player_2}.'),'Championship Court: Alex versus Sam.');
    assert.throws(()=>textFor(match,'{unknown}'));
});
test('serial queue, match ID duplicate suppression and refresh protection',()=>{
    const h=harness(); assert.equal(h.service.enqueue(match),true);
    assert.equal(h.service.enqueue(match),false);
    h.service.enqueue({...match,match_id:9,court_name:'Court 9'});
    assert.equal(h.spoken.length,1);h.spoken[0].onend();assert.equal(h.spoken.length,1);
    h.advance();assert.equal(h.spoken.length,2);
    const refreshed=harness(h.storage);assert.equal(refreshed.service.enqueue(match),false);
    refreshed.service.enqueue(match,'REPLAY');assert.equal(refreshed.spoken.length,1);
});
test('mute cancels queue and stale callbacks cannot restart it',()=>{
    const h=harness();h.service.enqueue(match);h.service.enqueue({...match,match_id:9});
    h.service.mute();h.spoken[0].onend();h.advance();assert.equal(h.spoken.length,1);
    assert.equal(h.logs.at(-1).playback_status,'CANCELLED');
});
test('speech unsupported, error and logging failures never throw',()=>{
    const h=harness();h.service.speech=null;assert.equal(h.service.enqueue(match),false);assert.match(h.warnings[0],/unavailable/);
    const e=harness();e.service.log=()=>{throw Error('network');};assert.doesNotThrow(()=>e.service.enqueue(match));
    e.spoken[0].onerror({error:'not-allowed'});assert.equal(e.service.active,null);assert.match(e.warnings.at(-1),/playback failed/);
});
test('automatic assignments require opt-in, gesture and a new confirmed match',()=>{
    const h=harness();h.service.settings.auto=true;h.service.armed=false;
    h.service.observe([]);
    const court={id:8,name:match.court_name,match:{id:8,status:'STARTING',state_json:{ready_to_play:true},settings_json:{players_a:'Dominic / Chel',players_b:'Mike / John'}}};
    h.service.observe([court]);assert.equal(h.spoken.length,0);
    h.service.arm();h.service.observe([court]);assert.equal(h.spoken.length,0);
    h.service.observe([court,{...court,id:9,name:'Court 9',match:{...court.match,id:9}}]);assert.equal(h.spoken.length,1);
    h.service.observe([court]);assert.equal(h.spoken.length,1);
});
