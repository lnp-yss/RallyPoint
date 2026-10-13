/* Operator-computer PA service. No audio/scoring runs on court devices. */
'use strict';
(function (scope) {
    const DEFAULT_TEMPLATE = 'Now playing on {court}. {team_a} versus {team_b}. Players, please proceed to {court}.';
    const DEFAULTS = {enabled:true, auto:false, voice:'', language:'en', volume:1, rate:1, pitch:1, template:DEFAULT_TEMPLATE};
    function textFor(match, template = DEFAULT_TEMPLATE) {
        const teams = match.teams || {};
        const names = team => (teams[team] || []).map(p => String(p.name || '').trim()).filter(Boolean);
        const a = names('A'), b = names('B');
        const values = {court:match.court_name, team_a:a.join(' and '), team_b:b.join(' and '),
            team_a_player_1:a[0] || '', team_a_player_2:a[1] || '',
            team_b_player_1:b[0] || '', team_b_player_2:b[1] || ''};
        if (!values.court || !a.length || !b.length) throw Error('Court and actual player names are required for an announcement.');
        const text = template.replace(/\{([^{}]+)\}/g, (_,key) => {
            if (!(key in values)) throw Error('Unknown placeholder: {' + key + '}');
            return values[key];
        }).replace(/\s+and\s+(?=versus|\.)/g,' ').replace(/\s+/g,' ').replace(/\s+([.,!?])/g,'$1').trim();
        if (!text || text.length > 2000) throw Error('Announcement must contain 1–2000 characters.');
        return text;
    }
    function preferences(storage, key) {
        let value = {};
        try { value = JSON.parse(storage?.getItem(key) || '{}') || {}; } catch (_) { /* defaults */ }
        return {...DEFAULTS, ...value, enabled:value.enabled !== false, auto:value.auto === true,
            voice:typeof value.voice==='string' ? value.voice.slice(0,500) : '',
            language:typeof value.language==='string' && value.language ? value.language.slice(0,100) : 'en',
            template:typeof value.template==='string' && value.template ? value.template.slice(0,2000) : DEFAULT_TEMPLATE,
            volume:Number.isFinite(value.volume) ? Math.min(1,Math.max(0,value.volume)) : 1,
            rate:Number.isFinite(value.rate) ? Math.min(2,Math.max(.5,value.rate)) : 1,
            pitch:Number.isFinite(value.pitch) ? Math.min(2,Math.max(0,value.pitch)) : 1};
    }
    class AnnouncementService {
        constructor({speech, Utterance, storage, key, log=()=>{}, status=()=>{}, timer=(fn,ms)=>setTimeout(fn,ms), clear=id=>clearTimeout(id), uuid}) {
            Object.assign(this,{speech,Utterance,storage,key,log,status,timer,clear,uuid});
            this.settings=preferences(storage,key); this.queue=[]; this.active=null; this.armed=false;
            this.seen=new Set(); this.observed=null;
            try { JSON.parse(storage?.getItem(key+'.seen') || '[]').slice(-500).forEach(id=>this.seen.add(String(id))); } catch (_) { /* storage optional */ }
        }
        available() { return !!(this.speech && this.Utterance); }
        arm() { this.armed=true; try { this.speech?.resume(); } catch (_) {} }
        save(values) {
            const template=String(values.template || '').trim();
            textFor({court_name:'Sample Court',teams:{A:[{name:'Alex'},{name:'Sam'}],B:[{name:'Jamie'},{name:'Taylor'}]}},template);
            this.settings=preferences({getItem:()=>JSON.stringify({...this.settings,...values,template})},this.key);
            try { this.storage?.setItem(this.key,JSON.stringify(this.settings)); } catch (_) { this.status('Browser storage unavailable; preferences last until this page closes.'); }
            if (!this.settings.enabled) this.stop();
        }
        mute() { this.save({...this.settings,enabled:!this.settings.enabled}); }
        report(item,playback_status,error='') {
            if (item.type==='TEST') return;
            try { Promise.resolve(this.log({event_id:item.event_id,match_id:item.match.match_id,
                announcement_type:item.type,playback_status,error})).catch(()=>{}); } catch (_) { /* never block scoring */ }
        }
        remember(id) {
            this.seen.add(String(id));
            const recent=[...this.seen].slice(-500); this.seen=new Set(recent);
            try { this.storage?.setItem(this.key+'.seen',JSON.stringify(recent)); } catch (_) {}
        }
        enqueue(match,type='MATCH_START') {
            if (!this.settings.enabled && type!=='TEST') return false;
            if (!this.available()) { this.status('Match started successfully, but voice announcement is unavailable on this browser.'); return false; }
            if (!this.armed) { this.status('Enable PA audio on this computer before automatic announcements.'); return false; }
            if (type==='MATCH_START' && this.seen.has(String(match.match_id))) return false;
            if (this.queue.length>=50) { this.status('Announcement queue is full. Use Replay Announcement when it clears.'); return false; }
            let text;
            try { text=textFor(match,this.settings.template); } catch (error) { this.status(error.message); return false; }
            if (type==='MATCH_START') this.remember(match.match_id);
            const item={match,type,text,event_id:this.uuid(),settings:{...this.settings}};
            this.queue.push(item); this.pump(); return true;
        }
        pump() {
            if (this.active || this.gap || !this.queue.length) return;
            const item=this.queue.shift(); this.active=item;
            const utterance=new this.Utterance(item.text), settings=item.settings;
            item.utterance=utterance;
            utterance.lang=settings.language; utterance.volume=settings.volume; utterance.rate=settings.rate; utterance.pitch=settings.pitch;
            const voices=this.speech.getVoices?.() || [];
            utterance.voice=voices.find(v=>v.voiceURI===settings.voice) || voices.find(v=>v.localService && v.lang?.toLowerCase().startsWith(settings.language.toLowerCase())) || voices.find(v=>v.lang?.toLowerCase().startsWith(settings.language.toLowerCase())) || voices.find(v=>v.localService) || null;
            let started=false;
            const finish=(result,error='')=>{
                if (this.active!==item) return;
                this.clear(item.watchdog); this.report(item,result,error); this.active=null;
                if (result==='FAILED') {
                    try { this.speech.cancel(); } catch (_) {}
                    this.status(item.type==='TEST' ? 'Voice playback failed. Check browser permissions and audio output.' : 'Match started successfully, but voice playback failed. Check audio output and use Replay Announcement.');
                } else this.status(this.queue.length ? 'Next announcement queued…' : 'Announcements ready');
                this.gap=this.timer(()=>{this.gap=null;this.pump();},350);
            };
            utterance.onstart=()=>{
                if (this.active!==item) return;
                started=true; this.clear(item.watchdog); this.report(item,'STARTED');
                this.status('Announcing '+item.match.court_name+'…');
                item.watchdog=this.timer(()=>finish('FAILED','playback-timeout'),Math.min(180000,Math.max(12000,item.text.length*160/settings.rate+5000)));
            };
            utterance.onend=()=>finish(started?'COMPLETED':'FAILED',started?'':'playback-not-started');
            utterance.onerror=event=>finish('FAILED',event.error || 'speech-error');
            item.watchdog=this.timer(()=>finish('FAILED','audio-not-unlocked'),7000);
            try { this.speech.speak(utterance); } catch (error) { finish('FAILED','speech-unavailable'); }
        }
        stop() {
            this.queue=[]; if(this.gap) {this.clear(this.gap);this.gap=null;}
            if(this.active) { const item=this.active;this.active=null;this.clear(item.watchdog);this.report(item,'CANCELLED'); }
            try { this.speech?.cancel(); } catch (_) {}
            this.status('Announcements muted');
        }
        observe(courts) {
            const confirmed=courts.filter(c=>c.match && (c.match.status==='ACTIVE' || c.match.state_json?.ready_to_play));
            const current=new Set(confirmed.map(c=>String(c.match.id)));
            if(this.observed && this.settings.auto && this.armed && this.settings.enabled) {
                for(const c of confirmed) if(!this.observed.has(String(c.match.id))) {
                    const m=c.match,s=m.settings_json;
                    this.enqueue({match_id:m.id,court_id:c.id,court_name:c.name,teams:{A:(s.players_a || '').split(' / ').filter(Boolean).map(name=>({name})),B:(s.players_b || '').split(' / ').filter(Boolean).map(name=>({name}))}});
                }
            }
            this.observed=current;
        }
    }
    if(typeof module!=='undefined' && module.exports) module.exports={AnnouncementService,textFor,DEFAULTS};
    if(!scope.document) return;
    let storage;
    try {storage=scope.localStorage;} catch (_) {}
    const prefix=document.body.dataset.base || '';
    const service=new AnnouncementService({speech:scope.speechSynthesis,Utterance:scope.SpeechSynthesisUtterance,
        storage,key:'rallypoint.pa.v1'+prefix,uuid:()=>scope.crypto.randomUUID ? scope.crypto.randomUUID() : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=scope.crypto.getRandomValues(new Uint8Array(1))[0]&15;return(c==='x'?r:(r&3)|8).toString(16);}),
        log:data=>fetch(prefix+'/api/announcements/events',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},body:JSON.stringify(data)}),
        status:message=>{const target=document.querySelector('#pa-status');if(target) target.textContent=message;}
    });
    scope.RallyAnnouncements=service;
    // Gesture is recorded only on this operator document; spectators never arm PA.
    if(['ADMIN','OPERATOR'].includes(document.body.dataset.role)) {
        document.addEventListener('pointerdown',()=>service.arm(),{once:true});
        document.addEventListener('keydown',()=>service.arm(),{once:true});
        const bar=document.createElement('div');bar.className='pa-toolbar';
        const indicator=document.createElement('span'), status=document.createElement('span');status.id='pa-status';status.setAttribute('role','status');
        const mute=document.createElement('button');mute.type='button';
        const audio=document.createElement('button');audio.type='button';audio.textContent='TEST / ENABLE PA AUDIO';
        const link=document.createElement('a');link.href=prefix+'/settings/announcements';link.textContent='Announcement Settings';
        const refresh=()=>{indicator.textContent=service.settings.enabled?'SPEAKER · ANNOUNCEMENTS ON':'SPEAKER · ANNOUNCEMENTS MUTED';mute.textContent=service.settings.enabled?'MUTE ANNOUNCEMENTS':'UNMUTE ANNOUNCEMENTS';};
        mute.onclick=()=>{service.arm();service.mute();refresh();};
        audio.onclick=()=>{service.arm();service.enqueue({court_name:'Sample Court',teams:{A:[{name:'Alex'},{name:'Sam'}],B:[{name:'Jamie'},{name:'Taylor'}]}},'TEST');};
        bar.append(indicator,mute,audio,link,status);document.querySelector('nav')?.after(bar);refresh();
        service.refreshIndicator=refresh;
        if(!service.available()) status.textContent='Voice announcement is unavailable on this browser.';
    }
    scope.addEventListener('pagehide',()=>service.stop());
})(typeof window==='undefined' ? globalThis : window);
