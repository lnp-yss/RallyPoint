/* Browser beeps only after HDMI acceptance, armed by a user gesture. */
'use strict';
(() => {
    let context = null;
    const played = new Set();
    function arm() {
        const Audio = window.AudioContext || window.webkitAudioContext;
        if (!Audio) return;
        try {
            context ||= new Audio();
            if(context.state==='suspended') context.resume().catch(()=>{});
        } catch (_) {}
    }
    function tone() {
        if(!context || context.state!=='running') return false;
        const oscillator=context.createOscillator(),gain=context.createGain();
        const now=context.currentTime;
        oscillator.frequency.value=880;
        gain.gain.setValueAtTime(0,now);
        gain.gain.linearRampToValueAtTime(.15,now+.008);
        gain.gain.linearRampToValueAtTime(0,now+.09);
        oscillator.connect(gain);gain.connect(context.destination);
        oscillator.start(now);oscillator.stop(now+.1);
        oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();};
        return true;
    }
    async function confirmed(id) {
        if(!id || played.has(id)) return;
        played.add(id);
        if(played.size>500)played.delete(played.values().next().value);
        try {
            const base=document.body.dataset.base || '';
            const response=await fetch(base+'/api/score-sounds');
            if(!response.ok)return;
            const data=await response.json();
            if(['operator','both'].includes(data.output))tone();
        } catch (_) {}
    }
    window.RallyScoreSounds={arm,confirmed,test:async()=>{arm();if(context?.state==='suspended')await context.resume();return tone();}};
})();
