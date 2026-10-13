/* Session matchmaking controls. All DOM text uses textContent. */
const modeLabels = {
    FAIR_BALANCED: 'Fair / Balanced', QUEUE: 'Queue / First come first served',
    RANDOM: 'Random', SKILL_BASED: 'Skill-based', WINNERS_STAY: 'Winners stay',
    KING_OF_COURT: 'King of Court · future promotion/demotion', MANUAL: 'Manual matching'
};

function groupedQueue(players, actions = true, dashboard = false) {
    const groups = new Map();
    for (const p of players) {
        const key = p.group_id || p.id;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(p);
    }
    if (!groups.size) return el('p', 'No players waiting.', {class:'empty'});
    const list = el('div');
    for (const [id, members] of groups) {
        const first = members[0], controls = el('div', '', {class:'row'});
        const name = members.map(p => p.display_name).join(' / ');
        if (actions) {
            for (const [label, action] of [['↑ Up','up'],['↓ Down','down'],['Priority','priority'],['Remove','remove']]) {
                controls.append(button(label, async () => {
                    if (action === 'remove' && !confirm('Remove the entire entry: ' + name + '?')) return;
                    await api('/api/rotation/groups/' + id, {action}); await loadQueue();
                }));
            }
            if (first.entry_type === 'SOLO') {
                const peers = [...groups].filter(([key, people]) => key !== id && people[0].entry_type === 'SOLO');
                const select = field('Partner', 'partner', '', [['','Select waiting solo'], ...peers.map(([key,people]) => [key,people[0].display_name])]);
                controls.append(select, button('PAIR TEMPORARY TEAM', async () => {
                    const partner_id = Number(select.querySelector('select').value);
                    if (!partner_id) throw Error('Select a waiting partner');
                    await api('/api/rotation/groups/' + id, {action:'pair',partner_id}); await loadQueue();
                }));
            } else if (first.temporary) controls.append(button('BREAK TEMPORARY TEAM', async () => {
                if (!confirm('Return both players to separate solo entries?')) return;
                await api('/api/rotation/groups/' + id, {action:'break',confirmed:true}); await loadQueue();
            }, 'danger'));
        }
        const quick = el('div','',{class:'row dashboard-queue-actions'});
        if (dashboard && ['ADMIN','OPERATOR'].includes(role)) {
            for (const action of ['priority','remove']) {
                const control = button(action === 'priority' ? 'Priority' : 'Remove', async () => {
                    if (action === 'remove' && !confirm('Remove this waiting entry: ' + name + '?')) return;
                    if (first.group_id) await api('/api/rotation/groups/' + first.group_id,{action});
                    else await api('/api/queue/' + first.id,{action});
                    await loadDashboard();
                },action === 'priority' && first.priority ? 'primary' : action === 'remove' ? 'danger' : '');
                if (action === 'priority') control.setAttribute('aria-pressed',first.priority ? 'true' : 'false');
                quick.append(control);
            }
        }
        list.append(el('div','',{class:'queue-row'}, [el('div','',{},[
            el('strong',name), badge(first.entry_type + (first.temporary ? ' · TEMPORARY' : '')),
            el('p',Math.floor(Math.max(...members.map(p => p.waiting_minutes))) + ' min waiting · ' + members.map(p => (p.skill_rating ?? 'unrated') + ' / ' + p.games_today + ' games today').join(' · '),{class:'muted'}),
            dashboard ? quick : el('p',members.map(p => (members.length > 1 ? p.display_name + ': ' : '') + (p.matches_completed ?? 0) + ' matches completed').join(' · '),{class:'queue-match-count'})
        ]),controls]));
    }
    return list;
}

async function loadRotationCheckin() {
    const players = await api('/api/players');
    const choices = [['','Select player'], ...players.filter(p => p.status === 'ACTIVE').map(p => [p.id,p.display_name])];
    const form = el('form'), type = field('Join rotation','type','SOLO',[['SOLO','Join solo'],['TEAM','Join as fixed team']]);
    const first = field('Player','first','',choices), second = field('Partner','second','',choices);
    second.hidden = true;
    type.querySelector('select').onchange = e => second.hidden = e.target.value === 'SOLO';
    form.append(type,first,second,button('JOIN ROTATION',async () => {
        const data = formData(form), ids = [Number(data.first)];
        if(data.type === 'TEAM') ids.push(Number(data.second));
        await api('/api/rotation/checkin',{player_ids:ids}); await loadRotationCheckin(); notify('Joined rotation');
    },'primary'));
    root.replaceChildren(title('PLAYER ROTATION','Check in','Fixed teams remain together in every matching mode.'),card('Join solo / Join as team',[form]),link('Manage queue','/queue'));
}

async function loadRotationCourts() {
    const data = await api('/api/rotation');
    const count = field('Number of enabled courts','count',data.courts.filter(c => c.enabled).length,null,'number');
    const rows = data.courts.map(c => {
        const form = el('form');
        form.append(el('p','Database ID ' + c.id + ' · Court number ' + c.number + ' · ' + c.status),
            field('Court name','name',c.name),field('Enabled','enabled',c.enabled ? 'yes':'no',[['yes','Yes'],['no','No']]),
            field('Court level (future King of Court)','level',c.level,null,'number'),
            button('SAVE COURT',async () => {
                const d = formData(form); await api('/api/rotation/courts',{id:c.id,name:d.name,enabled:d.enabled==='yes',level:Number(d.level)});
                await loadRotationCourts();
            }),button('MARK AVAILABLE',async () => {
                await api('/api/rotation/courts',{id:c.id,mark_available:true}); await loadRotationCourts();
            }));
        return card(c.name,[form,el('p',c.online ? 'Device online':'Device offline · new matches blocked',{class:'muted'}),
            el('p','Last heartbeat: ' + (c.last_heartbeat ? new Date(c.last_heartbeat).toLocaleString() : 'Never')),
            el('p','Assigned match: ' + (c.active_match_id ? '#' + c.active_match_id : 'None')),
            c.active_match_id ? link('VIEW MATCH','/control?court=' + c.id) : null]);
    });
    root.replaceChildren(title('CLUB CONFIGURATION','Court Settings','Court numbers and database IDs are preserved. Occupied courts cannot be disabled.'),
        card('Active court count',[count,button('APPLY COURT COUNT',async () => {
            await api('/api/rotation/courts',{count:Number(count.querySelector('input').value)}); await loadRotationCourts();
        },'primary')]),el('div','',{class:'grid'},rows),link('Matchmaking Settings','/settings/matchmaking'));
}

async function loadRotationSettings() {
    const data = await api('/api/rotation'), settings = data.settings, form = el('form','',{class:'rotation-form'});
    const mode = field('Matchmaking mode','mode',settings.mode,Object.entries(modeLabels));
    const relevant = el('div','',{class:'grid'}), scope = el('div');
    for (const c of data.courts.filter(c => c.enabled)) {
        const input = el('input','',{type:'checkbox',value:c.id,name:'courts'});
        input.checked = !data.session?.court_ids_json?.length || data.session.court_ids_json.includes(c.id);
        scope.append(el('label',c.name,{},[input]));
    }
    const fields = {
        waiting_weight:'Waiting time (%)',skill_weight:'Skill balance (%)',partner_weight:'Partner variety (%)',opponent_weight:'Opponent variety (%)',games_weight:'Games fairness (%)',
        excellent_difference:'Excellent rating difference',good_difference:'Good rating difference',maximum_rating_difference:'Maximum initial rating difference',preferred_rating_difference:'Preferred rating difference',
        minimum_match_quality:'Minimum quality (0–100)',long_wait_minutes:'Long wait (minutes)',maximum_wait_override_minutes:'Maximum wait override (minutes)',grace_period_seconds:'Grace period (seconds)',
        maximum_consecutive_wins:'Maximum consecutive wins',candidate_limit:'Candidate search limit',heartbeat_timeout_seconds:'Heartbeat timeout (seconds)',default_rating:'Default unrated skill'
    };
    function renderMode() {
        // Preserve unsaved values while changing mode; inactive fields keep saved values.
        for(const input of relevant.querySelectorAll('input')) settings[input.name] = Number(input.value);
        relevant.replaceChildren();
        const value = mode.querySelector('select').value;
        let keys = value === 'MANUAL' || value === 'KING_OF_COURT' ? [] : ['maximum_wait_override_minutes','candidate_limit'];
        if(['FAIR_BALANCED','SKILL_BASED','WINNERS_STAY'].includes(value)) keys.push('maximum_rating_difference','minimum_match_quality','long_wait_minutes','grace_period_seconds','default_rating');
        if(value === 'FAIR_BALANCED') keys.push('waiting_weight','skill_weight','partner_weight','opponent_weight','games_weight','excellent_difference','good_difference');
        if(value === 'SKILL_BASED') keys.push('preferred_rating_difference');
        if(value === 'WINNERS_STAY') keys.push('maximum_consecutive_wins');
        for(const key of keys) {const f = field(fields[key],key,settings[key],null,'number'); f.querySelector('input').step='any'; relevant.append(f);}
        if(value === 'KING_OF_COURT') relevant.append(el('p','Advanced promotion/demotion is a future feature. Automatic matching is unavailable for this mode.'));
        if(value === 'WINNERS_STAY') relevant.append(el('p','Winners return to rotation after the consecutive-win limit.'));
    }
    mode.querySelector('select').onchange = renderMode; renderMode();
    form.append(field('Session name','name',data.session?.name || 'Open Play'),mode,relevant,
        field('Automatically assign and send matches','automatic_assignment',settings.automatic_assignment?'yes':'no',[['no','No · operator review'],['yes','Yes · run every 5 seconds']]),
        field('Heartbeat timeout (seconds)','heartbeat_timeout_seconds',settings.heartbeat_timeout_seconds,null,'number'),
        field('Scoring mode','scoring_mode',settings.scoring_mode,['Side-Out','Rally']),
        field('Target score','points_to_win',settings.points_to_win,[11,15,21]),field('Win by','win_by',settings.win_by,[1,2]),
        field('Match format','best_of',settings.best_of,[[1,'Single game'],[3,'Best of 3'],[5,'Best of 5']]),
        el('h3','Session courts'),scope,el('p','Fixed teams always stay together. Playing and reserved matches retain their original rules.'),
        button('SAVE SETTINGS',async () => {
            const d = formData(form), saved = {...settings,mode:d.mode,automatic_assignment:d.automatic_assignment==='yes',scoring_mode:d.scoring_mode};
            for(const input of form.querySelectorAll('input[type="number"]')) saved[input.name]=Number(input.value);
            for(const key of ['points_to_win','win_by','best_of']) saved[key]=Number(d[key]);
            const court_ids = [...scope.querySelectorAll('input:checked')].map(i => Number(i.value));
            if(!court_ids.length) throw Error('Select at least one enabled court');
            await api('/api/rotation/settings',{name:d.name,settings:saved,court_ids}); await loadRotationSettings(); notify('Settings saved');
        },'primary'));
    root.replaceChildren(title('CLUB CONFIGURATION','Matchmaking Settings','Choose how the next matches are formed.'),card('Active play session',[form]),
        link('Court Settings','/settings/courts'),card('Assignments',[
            button('FORCE MATCHMAKING RUN',async () => {const r = await api('/api/rotation/run',{}); await loadRotationSettings(); notify(r.matches.length + ' matches reserved');}),
            link('MANUAL MATCHING','/setup'),await reservationCards(data)
        ]));
}

async function reservationCards(data) {
    const node = el('div');
    for(const m of data.dashboard.matches.filter(m => ['PREPARED','RESERVED','STARTING'].includes(m.status))) {
        const court = data.courts.find(c => c.id === m.court_id);
        const details = el('details','',{},[el('summary','Selection explanation'),el('pre',JSON.stringify(m.matchmaking_json?.explanation || {},null,2))]);
        const controls = el('div','',{class:'row'});
        if(m.status !== 'STARTING') {
            controls.append(button('START MATCH',async () => {
                const r = await api('/api/rotation/matches/' + m.id,{action:'start'}); await awaitReceipt(r,m.court_id); await loadRotationSettings();
            },'primary'),button('CANCEL ASSIGNMENT',async () => {
                if(!confirm('Cancel this assignment and return its players to rotation?')) return;
                await api('/api/rotation/matches/' + m.id,{action:'cancel'}); await loadRotationSettings();
            },'danger'));
            const next = field('Move to court','court',data.courts.find(c => c.status==='AVAILABLE')?.id || '',data.courts.filter(c => c.status==='AVAILABLE').map(c => [c.id,c.name]));
            controls.append(next,button('REASSIGN',async () => {
                await api('/api/rotation/matches/' + m.id,{action:'reassign',court_id:Number(next.querySelector('select').value)}); await loadRotationSettings();
            }));
        } else controls.append(el('p','Awaiting receipt/reconciliation. Check live state; release safely from Match setup.'));
        node.append(card('#' + m.id + ' · ' + (court?.name || '') + ' · ' + m.status,[el('p',names(m.settings_json).join(' vs ') + ' · ' + (modeLabels[m.matchmaking_mode] || m.matchmaking_mode)),details,controls]));
    }
    return node;
}
