'use strict';
let courtManagerTimer = null;
let networkSettingsTimer = null;
let networkSettingsDraft = null;
const root = document.querySelector('#app'),
    role = document.body.dataset.role;
let page = document.body.dataset.page;
let overviewTimer = null;
const workspaceBase = document.body.dataset.base || '';
function workspacePath(path) { return path.startsWith('/') && !path.startsWith('//') ? workspaceBase + path : path; }
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const params = new URLSearchParams(location.search);
let clubDisplayName = document.querySelector('.brand span:last-child')?.firstChild?.textContent?.trim() || 'RALLY POINT PICKLEBALL CLUB';
let latest = null,
    eventSource = null;
let leaderboardMode = params.get('view') === 'leaderboard',
    leaderboardTimer = null;

function commandId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' + hex.slice(16, 20) + '-' + hex.slice(20);
}

function el(tag, text = '', attrs = {}, children = []) {
    const node = document.createElement(tag);
    node.textContent = text;
    for (const [k, v] of Object.entries(attrs)) {
        if (k === 'class') node.className = v;
        else node.setAttribute(k, (k === 'href' || k === 'src') ? workspacePath(String(v)) : v);
    }
    for (const child of children)
        if (child) node.append(child);
    return node;
}

function notify(text) {
    document.querySelector('#notification').textContent = text;
}
async function api(path, data) {
    const response = await fetch(workspacePath(path), {
        method: data ? 'POST' : 'GET',
        headers: data ? {
            'Content-Type': 'application/json',
            'X-CSRF-Token': csrf
        } : {},
        body: data ? JSON.stringify(data) : undefined
    });
    let value;
    try {
        value = await response.json();
    } catch {
        throw Error('Unexpected response. Check server status.');
    }
    if (!response.ok) throw Error(value.error || 'Request failed');
    return value;
}

function button(label, fn, style = '') {
    const b = el('button', label, {
        type: 'button',
        class: style
    });
    b.addEventListener('click', async () => {
        b.disabled = true;
        try {
            await fn();
        } catch (e) {
            notify(e.message);
        } finally {
            b.disabled = false;
        }
    });
    return b;
}

function link(label, path) {
    return el('a', label, {
        href: path,
        class: 'button'
    });
}

function title(eyebrow, name, detail) {
    return el('section', '', {}, [el('p', eyebrow, {
        class: 'eyebrow'
    }), el('h1', name), el('p', detail, {
        class: 'muted'
    })]);
}

function badge(text, live = false) {
    return el('span', text, {
        class: 'badge' + (live ? ' live' : '')
    });
}

function card(name, children, style = '') {
    return el('section', '', {
        class: 'card ' + style
    }, [el('h2', name), ...children]);
}

function field(label, name, value = '', choices = null, type = 'text') {
    const input = choices ? el('select', '', {
        name
    }) : el('input', '', {
        name,
        type
    });
    if (choices) {
        for (const c of choices) {
            const [v, t] = Array.isArray(c) ? c : [c, c];
            input.append(el('option', t, {
                value: v
            }));
        }
    }
    input.value = value;
    return el('label', label, {}, [input]);
}

function formData(form) {
    return Object.fromEntries(new FormData(form));
}

function numberFields(data, fields) {
    for (const key of fields) data[key] = Number(data[key]);
    return data;
}

function elapsed(start, end = null) {
    if (!start) return '00:00';
    const seconds = Math.max(0, Math.floor(((end ? new Date(typeof end === 'number' ? end * 1000 : end).getTime() : Date.now()) - new Date(typeof start === 'number' ? start * 1000 : start).getTime()) / 1000));
    return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
}

function names(settings) {
    return [settings.players_a || settings.team_a, settings.players_b || settings.team_b];
}

function scoreboard(data) {
    const board = el('section', '', {
        class: 'scoreboard'
    });
    if (!data.settings || !data.state) {
        board.append(el('p', 'Waiting for the HDMI scoreboard…', {
            class: 'empty'
        }));
        return board;
    }
    const s = data.settings,
        t = data.state,
        g = t.game;
    board.append(el('header', '', {}, [el('h1', 'RALLY POINT'), el('p', s.club_name), el('p', s.court_name || 'COURT SCOREBOARD')]));
    const panels = el('div', '', {
        class: 'score-panels'
    });
    for (const team of ['A', 'B']) {
        const low = team.toLowerCase();
        const panel = el('section', '', {
            class: 'score-panel ' + low + (g.serving === team ? ' serving' : '')
        });
        const side = data.indicators?.service_side || g.service_side;
        if (g.serving === team && t.status === 'playing' && ['Left','Right'].includes(side)) {
            panel.append(el('div',side.toUpperCase() + ' SERVICE', {
                class:'service-position ' + side.toLowerCase(),
                'aria-label':'Serve from the ' + side.toLowerCase() + ' service court, from the server perspective'
            }));
        }
        panel.append(el('h2', s['team_' + low]), el('div', String(g.scores[team]), {
            class: 'score-number'
        }), el('div', s['players_' + low], {
            class: 'score-players'
        }), el('div', '', {
            class: 'score-meta'
        }, [el('span', 'GAMES ' + t.games_won[team]), el('span', 'TIMEOUTS ' + (s.timeout_limit - g.timeouts[team]))]));
        panels.append(panel);
    }
    board.append(panels);
    const serve = el('div', '', {
        class: 'serve-call ' + g.serving.toLowerCase()
    });
    for (let i = 0; i < g.server; i++) serve.append(el('span', '', {
        class: 'serve-dot',
        'aria-hidden': 'true'
    }));
    serve.append(document.createTextNode(' SERVING ' + s['team_' + g.serving.toLowerCase()]));
    board.append(serve);
    let call = g.scores[g.serving] + ' - ' + g.scores[g.serving === 'A' ? 'B' : 'A'];
    if (s.scoring_mode === 'Side-Out' && s.game_type === 'Doubles') call += ' - ' + g.server;
    board.append(el('div', call, {
        class: 'score-call'
    }), el('p', 'GAME ' + t.game_number + ' / ' + (s.best_of === 1 ? 'SINGLE GAME' : 'BEST OF ' + s.best_of) + ' / ' + s.scoring_mode.toUpperCase() + ' / ' + s.game_type.toUpperCase(), {
        class: 'score-footer'
    }));
    const marks = data.indicators || {};
    for (const key of ['game_point', 'match_point'])
        if (marks[key]?.length) board.append(el('p', key.replace('_', ' ').toUpperCase() + ': ' + marks[key].map(team => s['team_' + team.toLowerCase()]).join(', '), {
            class: 'score-footer team-b'
        }));
    board.append(el('p', 'ELAPSED ' + elapsed(data.started_at, data.ended_at) + ' / ' + (data.connected ? 'CONNECTED' : 'OFFLINE · LAST KNOWN STATE'), {
        class: 'score-footer'
    }));
    if (data.ready_to_play || t.status !== 'playing' || t.paused || data.timeout_team) {
        let caption = data.ready_to_play ? 'READY TO PLAY' : t.status === 'complete' ? 'MATCH WINNER' : t.status === 'game_over' ? 'GAME WON' : t.status === 'ended' ? 'MATCH ENDED' : t.paused ? 'INPUT PAUSED' : 'TIMEOUT';
        let who = t.winner ? s['team_' + t.winner.toLowerCase()] : data.timeout_team ? s['team_' + data.timeout_team.toLowerCase()] : '';
        board.append(el('div', who, {
            class: 'winner-banner'
        }, [el('small', caption)]));
    }
    return board;
}

function drawLive(data) {
    latest = data;
    const firstServeControl = document.querySelector('#change-first-serve');
    if (firstServeControl) firstServeControl.hidden = !data.ready_to_play;
    document.querySelector('#connection').textContent = data.connected ? '● LIVE' : 'CONNECTION LOST';
    const target = document.querySelector('#live-board');
    if (target && !leaderboardMode) target.replaceChildren(scoreboard(data));
}

function leaderboardRank(rank) {
    const cell = el('td');
    const medal = {1:'gold',2:'silver',3:'bronze'}[rank];
    cell.append(el('span',String(rank)));
    if (medal) cell.append(el('span', '', {class:'rank-medal '+medal,role:'img','aria-label':medal+' medal'}));
    return cell;
}

function leaderboardBoard(data) {
    const board = el('section', '', {class: 'leaderboard-board'}, [
        el('p', clubDisplayName, {class: 'eyebrow'}),
        el('h1', 'PLAYER LEADERBOARD'),
        el('p', data.scope, {class: 'muted'})
    ]);
    if (!data.players.length) {
        board.append(el('p', 'Rankings appear after registered players finish their first match.', {class: 'empty'}));
    } else {
        const table = el('table', '', {class: 'ranking-table'});
        const head = el('tr');
        for (const label of ['Rank', 'Player', 'Matches', 'Wins', 'Losses', 'Win %', 'Point diff.']) head.append(el('th', label, {scope: 'col'}));
        table.append(el('thead', '', {}, [head]));
        const body = el('tbody');
        for (const p of data.players) {
            const row = el('tr');
            row.append(leaderboardRank(p.rank));
            for (const value of [p.display_name, p.matches, p.wins, p.losses, p.win_percentage.toFixed(1) + '%', (p.point_difference > 0 ? '+' : '') + p.point_difference]) row.append(el('td', String(value)));
            body.append(row);
        }
        table.append(body);
        board.append(el('div', '', {class: 'table-scroll'}, [table]));
    }
    board.append(el('p', data.rules, {class: 'muted'}));
    return board;
}

async function refreshLeaderboard() {
    const data = await api('/api/leaderboard' + (page === 'leaderboard' ? '?' + params.toString() : ''));
    const target = document.querySelector('#live-board');
    if (leaderboardMode && target) target.replaceChildren(leaderboardBoard(data));
}

function presentationControls() {
    const selector = field('Display', 'display', leaderboardMode ? 'leaderboard' : 'scoreboard', [
        ['scoreboard', 'Live scoreboard'], ['leaderboard', 'Player leaderboard']
    ]);
    selector.querySelector('select').onchange = async event => {
        leaderboardMode = event.target.value === 'leaderboard';
        const url = new URL(location.href);
        url.searchParams.set('view', leaderboardMode ? 'leaderboard' : 'scoreboard');
        history.replaceState(null, '', url);
        const hidden = document.querySelector('input[name="view"]');
        if (hidden) hidden.value = leaderboardMode ? 'leaderboard' : 'scoreboard';
        const previewSettings = document.querySelector('#preview-settings');
        if (previewSettings) previewSettings.hidden = leaderboardMode;
        try {
            if (leaderboardMode) await refreshLeaderboard();
            else if (latest) drawLive(latest);
        } catch (error) { notify(error.message); }
    };
    if (leaderboardTimer) clearInterval(leaderboardTimer);
    leaderboardTimer = setInterval(() => {
        if (leaderboardMode) refreshLeaderboard().catch(error => notify(error.message));
    }, 5000);
    return el('div', '', {class: 'row presentation-controls'}, [selector,
        button('FULLSCREEN DISPLAY', async () => {
            await document.querySelector('#live-board').requestFullscreen();
        }), link('PLAYER RANKINGS', '/leaderboard')]);
}

function startEvents(court) {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(workspacePath('/api/events' + (court ? '?court=' + court : '')));
    eventSource.addEventListener('state', event => drawLive(JSON.parse(event.data)));
    eventSource.onerror = () => {
        document.querySelector('#connection').textContent = 'RECONNECTING…';
    };
}

function replayIcon(label, destination, action) {
    const control = button('', action, 'court-audio');
    control.title = 'Replay announcement on ' + label;
    control.setAttribute('aria-label', control.title);
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('aria-hidden','true');
    const path = document.createElementNS(svg.namespaceURI,'path');
    path.setAttribute('d','M3 9h4l5-4v14l-5-4H3z M16 8q5 4 0 8 M19 5q8 7 0 14');
    path.setAttribute('fill','none');path.setAttribute('stroke','currentColor');
    path.setAttribute('stroke-width','2');path.setAttribute('stroke-linejoin','round');
    svg.append(path);control.append(svg,el('span',destination));return control;
}

function lineupEditor(match) {
    const panel = el('details','',{class:'lineup-editor'});
    panel.append(el('summary','Change players'));
    const content = el('div','',{class:'lineup-fields'});panel.append(content);
    let loaded = false;
    panel.ontoggle = async () => {
        if (!panel.open || loaded) return;
        content.replaceChildren(el('p','Loading players...',{class:'muted'}));
        try {
            const data = await api('/api/matches/' + match.id + '/lineup');
            const inputs = {A:[],B:[]};
            const grid = el('div','',{class:'grid lineup-selectors'});
            const choices = data.choices.map(p=>[p.id,p.name]);
            for (const team of ['A','B']) data.players[team].forEach((id,index)=>{
                const item = field('Team ' + team + ' / Player ' + (index+1),'lineup_'+team+index,id,choices);
                inputs[team].push(item.querySelector('select'));grid.append(item);
            });
            const save = button('APPLY PLAYERS', async () => {
                const players = Object.fromEntries(['A','B'].map(t=>[t,inputs[t].map(i=>Number(i.value))]));
                if (new Set([...players.A,...players.B]).size !== players.A.length+players.B.length) throw Error('Select a different player for each position.');
                if (!confirm('Change this match players? Scores stay unchanged. The final result will count for the updated lineup.')) return;
                save.disabled = true;
                try {
                    await api('/api/matches/'+match.id+'/lineup',{players,expected_players:data.players,confirmed:true});
                    panel.open=false;await loadDashboard();
                    notify('Players updated. HDMI names sync automatically; scores and serving stay unchanged.');
                } finally {save.disabled=false;}
            },'primary');
            content.replaceChildren(grid,el('p','Scores and serve stay unchanged. Replaced players return to the waiting queue as solo entries. Final results count for the updated lineup.',{class:'muted lineup-note'}),
                el('div','',{class:'row'},[save,button('CANCEL',()=>{panel.open=false;})]));
            loaded=true;
        } catch(error) {content.replaceChildren(el('p',error.message,{class:'muted'}));}
    };
    return panel;
}

function courtCard(court) {
    const m = court.match,
        children = [badge(court.status, ['IN_USE','PLAYING'].includes(court.status))];
    if (m) {
        const s = m.settings_json,
            [a, b] = names(s);
        children.push(el('p', a, {
            class: 'team-a'
        }), el('p', 'vs', {
            class: 'muted'
        }), el('p', b, {
            class: 'team-b'
        }));
        const scores = m.state_json?.state?.game?.scores || {
            A: 0,
            B: 0
        };
        if (['ADMIN','OPERATOR'].includes(role) && ['STARTING','ACTIVE','COMPLETE','ENDED'].includes(m.status)) {
            children.push(replayIcon('Preview speaker', 'TV', async () => {
                await api('/api/announcements/preview-replay/' + m.id, {request_id:commandId()});
                notify('Replay requested on the Preview Pi speaker.');
            }), replayIcon('This device speaker', 'LOCAL', async () => {
                window.RallyAnnouncements?.arm();
                const info = await api('/api/announcements/matches/' + m.id);
                window.RallyAnnouncements?.enqueue(info,'REPLAY');
            }));
            if (page === 'dashboard' && ['STARTING','ACTIVE'].includes(m.status)) children.push(lineupEditor(m));
        }
        children.push(el('div', scores.A + ' – ' + scores.B, {
            class: 'court-score'
        }), el('small', elapsed(m.started_at, m.ended_at) + ' elapsed'), link('LIVE SCORE', '/live?court=' + court.id), link('CONTROLS', '/control?court=' + court.id));
    } else children.push(el('p', 'Ready for the next rally.', {
        class: 'muted'
    }), link('ASSIGN MATCH', '/setup?court=' + court.id));
    if (page === 'dashboard') {
        const actions = children.filter(node => ['A','BUTTON'].includes(node.tagName));
        const content = children.filter(node => !actions.includes(node));
        content.push(el('div','',{class:'row court-actions'},actions));
        return card(court.name,content,'accent court-monitor');
    }
    return card(court.name, children, 'accent');
}

function queueRows(players, actions = true, dashboard = false) {
    return groupedQueue(players, actions, dashboard);
}

function legacyQueueRows(players, actions = true) {
    if (!players.length) return el('p', 'No players waiting. Check in players to build the next match.', {
        class: 'empty'
    });
    const list = el('div');
    players.forEach((p, i) => {
        const info = el('div', '', {}, [el('div', '', {
            class: 'row'
        }, [el('span', i + 1, {
            class: 'queue-number'
        }), el('strong', p.display_name), badge(p.priority ? 'PRIORITY' : 'WAITING')]), el('p', Math.floor(p.waiting_minutes) + ' min waiting · Rating ' + (p.skill_rating ?? 'Unrated') + ' · ' + p.games_today + ' games this event', {
            class: 'wait'
        }), el('small', 'Last partner: ' + p.last_partner + ' / Opponent: ' + p.last_opponent)]);
        const controls = el('div', '', {
            class: 'row'
        });
        if (actions)
            for (const [label, action] of [
                    ['↑ Up', 'up'],
                    ['↓ Down', 'down'],
                    ['Priority', 'priority'],
                    ['Remove', 'remove']
                ]) controls.append(button(label, async () => {
                if (action === 'remove' && !confirm('Check out ' + p.display_name + '?')) return;
                await api('/api/queue/' + p.id, {
                    action
                });
                await loadQueue();
            }));
        list.append(el('div', '', {
            class: 'queue-row'
        }, [info, controls]));
    });
    return list;
}

let recommendationDraft = null;
const expandedRecommendations = new Set();
function suggestionCard(data) {
    const source = data.recommendations || (data.suggestion ? [data.suggestion] : []);
    const waiting = new Set(data.queue.map(p => p.id));
    if (!recommendationDraft) recommendationDraft = source.map(s => ({...s,team_a:[...s.team_a],team_b:[...s.team_b]}));
    if (!recommendationDraft.edited) recommendationDraft = source.map(s => ({...s,team_a:[...s.team_a],team_b:[...s.team_b]}));
    for (const draft of recommendationDraft) {
        for (const team of ['team_a','team_b']) draft[team] = draft[team].map(id => waiting.has(id) ? id : null);
        if (!data.courts.some(c => c.id === draft.court_id && c.status === 'AVAILABLE')) draft.court_id = null;
    }
    const container = el('div','',{class:'grid recommendation-cards'});
    const redraw = () => container.replaceWith(suggestionCard(data));
    for (let index=0;index<2;index++) {
        const draft = recommendationDraft[index];
        if (!draft) {
            container.append(card('Recommended match '+(index+1),[el('p','Waiting for enough eligible players. Doubles needs four different players per match.',{class:'muted'})]));
            continue;
        }
        const children = [badge(draft.court_id ? data.courts.find(c=>c.id===draft.court_id)?.name : 'NEXT AVAILABLE COURT'),
            el('p','Review players below. Remove only clears this match slot; the player stays in the queue. Keep fixed teams together.',{class:'muted'})];
        for (const [team,label] of [['team_a','Team A'],['team_b','Team B']]) {
            children.push(el('h3',label,{class:team==='team_a'?'team-a':'team-b'}));
            draft[team].forEach((id,slot) => {
                const used = new Set(recommendationDraft.flatMap(d=>[...d.team_a,...d.team_b]).filter(Boolean));
                const choices = [['','Select replacement / empty slot'],...data.queue.filter(p=>p.id===id || !used.has(p.id)).map(p=>[p.id,p.display_name])];
                const select = field(label+' player '+(slot+1),'recommendation-'+index+'-'+team+'-'+slot,id || '',choices);
                select.querySelector('select').onchange = event => {
                    draft[team][slot] = Number(event.target.value) || null;
                    recommendationDraft.edited = true; redraw();
                };
                children.push(el('div','',{class:'row'},[select,button('REMOVE',()=>{
                    draft[team][slot]=null; recommendationDraft.edited=true; redraw();
                })]));
            });
        }
        const court = field('Assign to available court','recommendation-court-'+index,draft.court_id || '',
            [['','Waiting for a court'],...data.courts.filter(c=>c.status==='AVAILABLE').map(c=>[c.id,c.name])]);
        court.querySelector('select').onchange = event => {draft.court_id=Number(event.target.value)||null;recommendationDraft.edited=true;redraw();};
        children.push(court);
        const ids = [...draft.team_a,...draft.team_b];
        const complete = ids.every(Boolean) && new Set(ids).size===ids.length;
        if (complete && draft.court_id) children.push(link('REVIEW & ASSIGN MATCH','/setup?court='+draft.court_id+'&a='+draft.team_a.join(',')+'&b='+draft.team_b.join(',')));
        else children.push(el('p',complete ? 'Players are ready. Choose a court when one becomes available.' : 'Fill every player slot before assigning this match.',{class:'muted'}));
        children.push(button('REGENERATE RECOMMENDATIONS',async()=>{
            recommendationDraft=null;
            if (page==='queue') await loadQueue(); else await loadDashboard();
        }));
        if (page === 'dashboard') {
            const namesFor = team => draft[team].map(id => data.queue.find(p=>p.id===id)?.display_name || 'Choose player').join(' / ');
            const editor = el('details','',{class:'recommendation-editor'},[
                el('summary','EDIT PLAYERS / CHOOSE COURT'),...children.filter(node=>node.tagName !== 'A')
            ]);
            editor.open = expandedRecommendations.has(index);
            editor.ontoggle = () => editor.open ? expandedRecommendations.add(index) : expandedRecommendations.delete(index);
            container.append(card('Recommended match '+(index+1),[
                badge(draft.court_id ? data.courts.find(c=>c.id===draft.court_id)?.name : 'NEXT AVAILABLE COURT'),
                el('p',namesFor('team_a'),{class:'team-a recommendation-team'}),
                el('p','vs',{class:'muted recommendation-versus'}),
                el('p',namesFor('team_b'),{class:'team-b recommendation-team'}),
                ...children.filter(node=>node.tagName === 'A'),editor
            ],'accent'));
        } else container.append(card('Recommended match '+(index+1),children,'accent'));
    }
    return container;
}
async function loadDashboard() {
    if (root.querySelector('.lineup-editor[open]')) return;
    if (page === 'dashboard' && root.contains(document.activeElement) && document.activeElement.matches('input,select')) return;
    const d = await api('/api/dashboard');
    if (document.visibilityState === 'visible') window.RallyAnnouncements?.observe(d.courts);
    const metrics = el('div', '', {
        class: 'metrics'
    });
    for (const [k, label] of [
            ['active_courts', 'ACTIVE COURTS'],
            ['waiting_players', 'WAITING PLAYERS'],
            ['matches_today', 'EVENT MATCHES'],
            ['checked_in', 'PLAYERS CHECKED IN']
        ]) metrics.append(el('div', '', {
        class: 'metric'
    }, [el('b', d.metrics[k]), el('span', label)]));
    const heading = el('header','',{class:'dashboard-heading'},[
        title(d.event ? d.event.name : 'COURT OPERATIONS', 'Dashboard', 'Assign the next match and monitor every court.'),
        link('SET UP MATCH','/setup')
    ]);
    const recent = el('details','',{class:'dashboard-history'},[
        el('summary','RECENT MATCHES'),
        ...d.matches.slice(0,5).map(m=>link('#'+m.id+' · '+names(m.settings_json).join(' vs ')+' · '+m.status,'/matches?id='+m.id))
    ]);
    root.replaceChildren(heading, metrics,
        el('section','',{class:'dashboard-section'},[el('h2','Courts'),el('div','',{class:'grid dashboard-courts'},d.courts.map(courtCard))]),
        el('section','',{class:'dashboard-section'},[el('h2','Next matches'),suggestionCard(d)]),
        card('Waiting queue',[el('div','',{class:'dashboard-waiting'},[queueRows(d.queue,false,true)])]),recent);

}
function quickPlayerForm() {
    const form = el('form', '', {id:'quick-player-form', class:'quick-player-form'});
    const first = field('First name', 'first_name');
    const display = field('Display name', 'display_name');
    const firstInput = first.querySelector('input'), displayInput = display.querySelector('input');
    firstInput.required = true; firstInput.maxLength = 80; displayInput.maxLength = 100;
    let customName = false, busy = false;
    firstInput.oninput = () => { if (!customName) displayInput.value = firstInput.value.trim(); };
    displayInput.oninput = () => { customName = Boolean(displayInput.value.trim()); };
    const last = field('Last name (optional)', 'last_name'); last.querySelector('input').maxLength = 80;
    const rating = field('Skill rating (optional)', 'skill_rating', '', null, 'number');
    Object.assign(rating.querySelector('input'), {min:'1', max:'8', step:'0.1'});
    const submit = el('button','ADD & CHECK IN',{type:'submit',class:'primary'});
    form.append(el('h3','Quick add player'),el('p','Add a new player directly to the waiting queue. First name becomes the display name.',{class:'muted'}),
        el('div','',{class:'grid'},[first,last,display,rating,field('Rating type','rating_type','Manual',['Manual','DUPR','Self-rated'])]),
        el('div','',{class:'actions'},[submit,link('CHECK IN AN EXISTING PLAYER','/checkin')]));
    form.onsubmit = async event => {
        event.preventDefault(); if (busy || !form.reportValidity()) return;
        busy = true; submit.disabled = true;
        try {
            const result = await api('/api/players/quick-checkin',formData(form));
            form.reset(); customName = false;
            notify(result.player.display_name + ' added and checked in');
            await loadQueue(); firstInput.focus();
        } catch (error) { notify(error.message); }
        finally { busy = false; submit.disabled = false; }
    };
    return form;
}
async function loadQueue() {
    const d = await api('/api/dashboard');
    const existing = root.querySelector('#waiting-player-list');
    if (existing) {
        existing.replaceChildren(queueRows(d.queue));
        root.querySelector('#queue-recommendation').replaceChildren(suggestionCard(d));
        return;
    }
    root.replaceChildren(title('PLAYER ROTATION', 'Waiting queue', 'Waiting time comes first. Manage priority and court opportunities.'),
        card('Waiting players', [quickPlayerForm(),el('div','',{id:'waiting-player-list'},[queueRows(d.queue)])]),
        el('div','',{id:'queue-recommendation'},[suggestionCard(d)]));
}
async function loadPlayers(checkin = false) {
    root.replaceChildren(title('CLUB COMMUNITY', checkin ? 'Player check-in' : 'Your players', checkin ? 'Find your player and join the rotation.' : 'Manual ratings, match results, and familiar faces.'));
    const search = field('Search players', 'search');
    const list = el('div', '', {
        class: 'grid'
    });
    root.append(search, list);
    if (!checkin && role === 'ADMIN') root.append(rosterImport());
    let timer;
    const refresh = async () => {
        const players = await api('/api/players?q=' + encodeURIComponent(search.querySelector('input').value));
        list.replaceChildren(...players.map(p => card(p.display_name, [badge(p.status), el('p', 'Rating ' + (p.skill_rating ?? 'Unrated') + ' · ' + p.rating_type, {
            class: 'muted'
        }), button(checkin ? 'CHECK IN' : 'VIEW PROFILE', async () => {
            if (checkin) {
                await api('/api/checkin', {
                    player_id: p.id
                });
                notify(p.display_name + ' checked in');
                await refresh();
            } else {
                await playerProfile(p.id);
            }
        }, 'primary'), ...(!checkin && role === 'ADMIN' ? [button('EDIT PLAYER', () => editPlayer(p))] : [])])));
    };
    search.querySelector('input').oninput = () => {
        clearTimeout(timer);
        timer = setTimeout(() => refresh().catch(e => notify(e.message)), 250);
    };
    await refresh();
    if (!checkin && role === 'ADMIN') root.append(button('CREATE PLAYER', () => editPlayer({
        status: 'ACTIVE'
    }), 'primary'));
}
async function editPlayer(p) {
    const form = el('form');
    const inputs = el('div', '', {
        class: 'grid'
    });
    for (const [k, label] of [
            ['first_name', 'First name'],
            ['last_name', 'Last name'],
            ['display_name', 'Display name'],
            ['nickname', 'Nickname'],
            ['gender', 'Gender'],
            ['skill_rating', 'Skill rating (optional)']
        ]) inputs.append(field(label, k, p[k] ?? ''));
    if (!p.id) {
        const firstName = inputs.querySelector('[name="first_name"]');
        const displayName = inputs.querySelector('[name="display_name"]');
        let customDisplayName = Boolean(displayName.value.trim());
        if (!customDisplayName) displayName.value = firstName.value.trim();
        firstName.addEventListener('input', () => {
            if (!customDisplayName) displayName.value = firstName.value.trim();
        });
        displayName.addEventListener('input', () => {
            customDisplayName = Boolean(displayName.value.trim());
        });
    }
    const ratingTypes = ['Manual', 'DUPR', 'Self-rated'];
    inputs.append(field('Rating type', 'rating_type', ratingTypes.includes(p.rating_type) ? p.rating_type : 'Manual', ratingTypes));
    inputs.append(field('Status', 'status', p.status === 'INACTIVE' ? 'INACTIVE' : 'ACTIVE', ['ACTIVE', 'INACTIVE']));
    form.append(inputs, el('button', 'SAVE PLAYER', {
        class: 'primary',
        type: 'submit'
    }));
    form.onsubmit = async event => {
        event.preventDefault();
        try {
            await api('/api/players' + (p.id ? '/' + p.id : ''), formData(form));
            await loadPlayers();
            notify('Player saved');
        } catch (e) {
            notify(e.message);
        }
    };
    const guide = el('section', '', {class: 'skill-rating-guide'}, [
        el('h3', 'Skill rating guide'),
        el('p', 'Use these informal club estimates when selecting Manual. They are not official ratings.', {class: 'muted'})
    ]);
    const levels = el('div', '', {class: 'skill-rating-levels'});
    for (const [rating, description] of [['2.0', 'New beginner'], ['2.5', 'Beginner · knows the rules'], ['3.0', 'Regular recreational player'], ['3.5', 'Strong intermediate'], ['4.0–4.5', 'Advanced player']]) {
        levels.append(el('div', '', {class: 'skill-rating-level'}, [el('strong', rating), el('span', description)]));
    }
    guide.append(levels,
        el('p', 'Manual: club estimate · DUPR: enter the player’s existing DUPR rating · Self-rated: player’s own estimate.'),
        el('p', 'Unsure? Leave Skill rating blank. Matchmaking uses its configured default (normally 3.0). Ratings are entered manually; external ratings are not fetched or verified.', {class: 'muted'}));
    root.replaceChildren(title('PLAYER DIRECTORY', p.id ? 'Edit player' : 'Create player', 'Ratings are manually entered; no external rating integration.'), card('Player details', [form, button('Cancel', () => loadPlayers()), guide]));
}
async function playerProfile(id) {
    const d = await api('/api/players/' + id),
        s = d.statistics;
    root.replaceChildren(title('PLAYER PROFILE', d.player.display_name, 'Current rating ' + (d.player.skill_rating ?? 'Unrated')), card('Performance', [el('p', s.matches + ' matches · ' + s.wins + ' wins · ' + s.losses + ' losses · ' + s.win_percentage + '% win rate'), el('p', s.games_won + ' games won · ' + s.games_lost + ' games lost'), el('p', s.points_for + ' points for · ' + s.points_against + ' against')]), card('Partners', Object.values(s.partners).map(p => el('p', p.name + ' · ' + p.games + ' games · ' + p.wins + ' match wins'))), card('Opponents', Object.values(s.opponents).map(p => el('p', p.name + ' · ' + p.games + ' games · ' + p.wins + ' wins / ' + p.losses + ' losses'))), card('Recent results', s.recent_results.map(r => link('#' + r.match_id + ' · ' + (r.won ? 'WIN' : 'LOSS'), '/matches?id=' + r.match_id))), button('Back to players', () => loadPlayers()));
}
async function awaitReceipt(receipt, court) {
    notify('Command queued. Waiting for HDMI confirmation…');
    for (let i = 0; i < 20; i++) {
        await new Promise(resolve => setTimeout(resolve, 150));
        const r = await api('/api/receipts/' + receipt.request_id + '?court=' + court);
        if (r.status === 'APPLIED') {
            notify('HDMI confirmed the action');
            if (receipt.announcement && window.RallyAnnouncements) {
                try { window.RallyAnnouncements.enqueue(receipt.announcement); }
                catch (_) { notify('Match started successfully, but voice announcement is unavailable on this browser.'); }
            }
            return;
        }
        if (r.status === 'REJECTED') throw Error(r.error || 'HDMI rejected the action');
    }
    throw Error('Confirmation pending. Check live state before submitting again.');
}
async function activateDashboard() {
    // Keep this document and its speech queue alive while changing the view.
    page = 'dashboard';
    document.body.dataset.page = page;
    history.replaceState(null, '', workspacePath('/'));
    for (const key of [...params.keys()]) params.delete(key);
    for (const anchor of document.querySelectorAll('nav a')) {
        const active = new URL(anchor.href).pathname === workspacePath('/');
        anchor.classList.toggle('active',active);
        if(active) anchor.setAttribute('aria-current','page');
        else anchor.removeAttribute('aria-current');
    }
    document.querySelector('.settings-menu > summary')?.classList.remove('active');
    document.querySelector('.settings-menu')?.removeAttribute('open');
    recommendationDraft = null;
    if(!overviewTimer) overviewTimer = setInterval(()=>loadDashboard().catch(error=>notify(error.message)),5000);
    try { await loadDashboard(); window.scrollTo({top:0,behavior:'smooth'}); }
    catch(error) {
        root.replaceChildren(title('MATCH ASSIGNED','Dashboard temporarily unavailable','The match was assigned successfully. You can retry loading this page.'),button('RETRY DASHBOARD',activateDashboard));
        notify(error.message);
    }
}

async function loadSetup() {
    const [players, courts] = await Promise.all([api('/api/players'), api('/api/courts')]);
    const form = el('form'),
        grid = el('div', '', {
            class: 'grid'
        });
    const rotation = await api('/api/rotation'), waiting = rotation.dashboard.queue;
    const choices = players.filter(p => ['ACTIVE','WAITING'].includes(p.status)).map(p => [p.id, p.display_name + ' · ' + (p.skill_rating ?? 'unrated') + (waiting.find(q => q.id === p.id)?.entry_type === 'TEAM' ? ' · FIXED TEAM ' + waiting.find(q => q.id === p.id).group_id : '')]);
    grid.append(field('Court', 'court_id', params.get('court') || courts.find(c => c.status === 'AVAILABLE')?.id, courts.filter(c => c.status === 'AVAILABLE').map(c => [c.id, c.name])), field('Match type', 'game_type', 'Doubles', ['Singles', 'Doubles']), field('Team A name', 'team_a', 'TEAM A'), field('Team B name', 'team_b', 'TEAM B'));
    for (const team of ['A', 'B'])
        for (let i = 1; i <= 2; i++) grid.append(field('Player ' + team + i, 'player_' + team + i, (params.get(team.toLowerCase()) || '').split(',')[i - 1] || '', [
            ["", 'Select player'], ...choices
        ]));
    for (const [label, key, value, options] of [
            ['Scoring type', 'scoring_mode', 'Side-Out', ['Side-Out', 'Rally']],
            ['Points to win', 'points_to_win', 11, [11, 15, 21]],
            ['Win by', 'win_by', 2, [1, 2]],
            ['Match format', 'best_of', 3, [
                [1, 'Single game'],
                [3, 'Best of 3'],
                [5, 'Best of 5']
            ]],
            ['First serving team', 'starting_team', 'Random', [
                ['Random', 'Random (fair draw)'],
                ['A', 'Team A'],
                ['B', 'Team B']
            ]],
            ['Starting side', 'starting_side', 'Right', ['Left', 'Right']],
            ['Timeouts per game', 'timeout_limit', 2, [0, 1, 2, 3, 4, 5]]
        ]) grid.append(field(label, key, key === 'starting_team' ? 'Random' : (rotation.settings[key] ?? value), options));
    grid.append(field('Optional notes', 'notes', ''));
    form.append(grid);
    const toggle = () => {
        const singles = form.elements.game_type.value === 'Singles';
        for (const team of ['A', 'B']) form.elements['player_' + team + '2'].disabled = singles;
    };
    form.elements.game_type.onchange = toggle;
    toggle();
    const save = async start => {
        const data = numberFields(formData(form), ['court_id', 'points_to_win', 'win_by', 'best_of', 'timeout_limit']);
        const payload = {
            court_id: data.court_id,
            settings: data,
            starting_side: data.starting_side,
            notes: data.notes,
            players: {
                A: [Number(data.player_A1)],
                B: [Number(data.player_B1)]
            }
        };
        if (data.game_type === 'Doubles') {
            payload.players.A.push(Number(data.player_A2));
            payload.players.B.push(Number(data.player_B2));
        }
        const match = await api('/api/matches', payload);
        notify('Match #' + match.id + ' saved');
        if (start) {
            const receipt = await api('/api/matches/' + match.id + '/start', {
                replace_confirmed: true
            });
            await awaitReceipt(receipt, data.court_id);
            await activateDashboard();
            return;
        }
        await preparedMatches();
    };
    form.append(el('div', '', {
        class: 'row'
    }, [button('SAVE MATCH', () => save(false)), button('START MATCH', async () => {
        if (confirm('Start this match on HDMI? An ongoing local match blocks assignment.')) await save(true);
    }, 'primary'), link('CANCEL', '/')]));
    const drafts = el('div', '', {
        id: 'prepared'
    });
    root.replaceChildren(title('COURT OPERATIONS', 'Set up the next match.', 'Choose players and rules, then send the match to HDMI.'), card('Match details', [form]), drafts);
    await preparedMatches();
}
async function preparedMatches() {
    const matches = await api('/api/matches');
    const target = document.querySelector('#prepared');
    if (!target) return;
    target.replaceChildren(card('Prepared / reserved matches', matches.filter(m => ['PREPARED', 'RESERVED', 'STARTING'].includes(m.status)).map(m => el('div', '', {
        class: 'queue-row'
    }, [el('span', '#' + m.id + ' · ' + names(m.settings_json).join(' vs ') + ' · ' + m.status), m.state_json?.ready_to_play ? link('CONFIRM ON CONTROLS', '/control?court=' + m.court_id) : button(['PREPARED','RESERVED'].includes(m.status) ? 'SEND TO COURT' : 'RELEASE RESERVATION', async () => {
        if (!confirm(['PREPARED','RESERVED'].includes(m.status) ? 'Send this match to the HDMI Ready to Play screen?' : 'Release only after live state confirms it did not start?')) return;
        if (['PREPARED','RESERVED'].includes(m.status)) {
            const r = await api('/api/matches/' + m.id + '/start', {
                replace_confirmed: true
            });
            await awaitReceipt(r, m.court_id);
            await activateDashboard();
            return;
        } else await api('/api/matches/' + m.id + '/release', {
            confirmed: true
        });
        await preparedMatches();
    }, ['PREPARED','RESERVED'].includes(m.status) ? 'primary' : 'danger')]))));
}
async function loadLive() {
    root.replaceChildren(title('COURTSIDE VIEW', 'Live scoreboard', 'The same authoritative match state as HDMI.'), presentationControls(), el('div', '', {id: 'live-board'}));
    drawLive(await api('/api/live' + (params.get('court') ? '?court=' + params.get('court') : '')));
    startEvents(params.get('court'));
    if (leaderboardMode) await refreshLeaderboard();
}

function courtsideReady(node, settings, live, assignedOnly = false) {
    node.classList.add('is-ready');
    node.append(el('div', '', {class: 'court-ready-title'}, [
        el('strong', 'READY TO PLAY'),
        el('span', assignedOnly ? 'ASSIGNED BY OPERATOR' : 'WAITING FOR COURT CONFIRMATION')
    ]));
    const first = settings.first_serving_team ||
        (['A', 'B'].includes(settings.starting_team) ? settings.starting_team : live.state?.game?.serving);
    const teams = el('div', '', {class: 'courtside-teams ready-teams'});
    for (const team of ['A', 'B']) {
        const low = team.toLowerCase();
        const panel = el('div', '', {class: 'courtside-team ' + low}, [
            el('h3', settings['team_' + low] || 'TEAM ' + team),
            el('p', settings['players_' + low] || 'Player names not set', {class: 'court-player-names'})
        ]);
        if (first === team) panel.append(el('div', '', {class: 'ready-first-serve'}, [
            el('span', '', {class: 'ready-ball', 'aria-hidden': 'true'}),
            el('strong', 'SERVES FIRST')
        ]));
        teams.append(panel);
    }
    const format = settings.best_of === 1 ? 'SINGLE GAME' : 'BEST OF ' + settings.best_of;
    node.append(teams, el('div', '', {class: 'ready-match-settings'}, [
        ...[settings.game_type, settings.scoring_mode + ' SCORING',
            settings.points_to_win + ' POINTS · WIN BY ' + settings.win_by, format]
            .map(text => el('span', text))
    ]), el('p', (settings.starting_team === 'Random' ? 'RANDOM FIRST-SERVE DRAW · ' : '') +
        (assignedOnly ? 'NEXT PLAYERS: PLEASE GO TO THIS COURT' : 'PLAYERS: GO TO THIS COURT'),
        {class: 'court-ready-instruction'}));
    if (!assignedOnly && !live.connected) node.append(el('p', 'DEVICE OFFLINE · LAST KNOWN ASSIGNMENT', {class: 'court-live-footer'}));
    return node;
}

function courtsideCourt(court) {
    const live = court.live || {}, settings = live.settings, state = live.state;
    const active = settings && state && (live.match_exists || live.match_id) && !['DISABLED', 'MAINTENANCE'].includes(court.status);
    const node = el('article', '', {class: 'courtside-court'}, [
        el('header', '', {class: 'split'}, [
            el('h2', 'COURT ' + court.number),
            badge(active ? (live.connected ? 'LIVE' : 'OFFLINE · LAST SCORE') : court.status, active && live.connected)
        ])
    ]);
    if (court.name.toLowerCase() !== ('court ' + court.number).toLowerCase()) node.append(el('p', court.name, {class: 'muted court-custom-name'}));
    if (court.assignment && court.status === 'RESERVED') {
        return courtsideReady(node, court.assignment.settings || court.assignment, live, true);
    }
    if (!active) {
        node.append(el('p', court.status === 'AVAILABLE' ? 'Ready for the next match' : court.status.replaceAll('_', ' '), {class: 'court-idle'}));
        return node;
    }
    if (live.ready_to_play) return courtsideReady(node, settings, live);
    const finished = ['complete', 'game_over', 'ended'].includes(state.status);
    if (finished) {
        node.classList.add('is-finished');
        const heading = node.querySelector('header');
        heading.classList.add('court-finished-header');
        heading.insertBefore(el('div', '', {class: 'court-finished-title'}, [
            el('strong', 'GAME FINISHED'),
            el('span', state.status === 'game_over' ? 'NEXT GAME PENDING' : 'FINAL SCORE')
        ]), heading.lastElementChild);
    }
    const game = state.game;
    const panels = el('div', '', {class: 'courtside-teams'});
    for (const team of ['A', 'B']) {
        const low = team.toLowerCase();
        const serving = state.status === 'playing' && game.serving === team;
        const winner = finished && state.winner === team;
        const panel = el('div', '', {class: 'courtside-team ' + low + (serving ? ' is-serving' : '') + (winner ? ' is-winner' : '')}, [
            el('h3', settings['team_' + low]),
            el('p', settings['players_' + low] || 'Player names not set', {class: 'court-player-names'}),
            el('strong', String(game.scores[team]), {class: 'courtside-score'}),
            el('small', 'GAMES ' + state.games_won[team])
        ]);
        if (winner) panel.append(el('span', 'WINNER', {class: 'court-winner-label'}));
        if (serving) panel.append(el('span', '', {class: 'court-serving'}, [
            ...Array.from({length: game.server}, () => el('span', '', {class: 'serve-dot', 'aria-hidden': 'true'})),
            el('span', 'SERVING')
        ]));
        panels.append(panel);
    }
    let status = 'GAME ' + state.game_number + ' · ' + settings.scoring_mode + ' · ' + (settings.best_of === 1 ? 'SINGLE GAME' : 'BEST OF ' + settings.best_of);
    if (state.status === 'complete') status = 'MATCH WINNER · ' + settings['team_' + state.winner.toLowerCase()];
    else if (state.status === 'game_over') status = 'GAME WON · ' + settings['team_' + state.winner.toLowerCase()];
    else if (state.status === 'ended') status = 'MATCH ENDED';
    else if (live.ready_to_play) status = 'READY TO PLAY · WAITING TO START';
    else if (state.paused) status = 'MATCH PAUSED';
    else if (live.timeout_team) status = 'TIMEOUT · ' + settings['team_' + live.timeout_team.toLowerCase()];
    const sync = live.sync || {};
    const age = live.updated_at ? Math.max(0, Math.floor(Date.now()/1000 - live.updated_at)) : null;
    status += ' · ' + (settings.play_mode || (live.match_id ? 'Club Managed' : 'Casual Play'));
    if (!live.connected && age !== null) status += ' · Last update ' + age + 's ago';
    if (sync.pending) status += ' · ' + sync.pending + ' pending sync';
    node.append(panels, el('p', status, {class: 'court-live-footer'}));
    return node;
}

function recommendedPanel(data) {
    const recommendations = data.recommendations || [data.recommended_match,data.on_deck].filter(Boolean);
    const section = el('section', '', {class:'courtside-next'}, [el('p','UP NEXT · ON DECK',{class:'eyebrow'})]);
    for (let index=0;index<2;index++) {
        const match = recommendations[index];
        const block = el('div','',{class:'preview-recommendation'},[el('h2','Recommended match '+(index+1))]);
        if (match) block.append(badge(match.court_name || (match.court_number ? 'COURT '+match.court_number : 'NEXT AVAILABLE COURT')),
            el('h3',match.team_a.join(' / '),{class:'team-a'}),el('p','VS',{class:'next-versus'}),
            el('h3',match.team_b.join(' / '),{class:'team-b'}));
        else block.append(el('p','Waiting for enough checked-in players.',{class:'muted next-note'}));
        section.append(block);
    }
    section.append(el('p','Recommended · Awaiting operator assignment',{class:'muted next-note'}),
        el('p',data.waiting_players + ' PLAYERS WAITING',{class:'next-waiting'}));
    return section;
}

function compactRanking(data) {
    const section = el('section', '', {class: 'courtside-ranking'}, [
        el('p', 'CLUB RANKINGS', {class: 'eyebrow'}), el('h2', 'Player leaderboard')
    ]);
    const players = data.players || [];
    const pageCount = Math.max(1, Math.ceil(players.length / 6));
    const index = Math.floor(Date.now() / 10000) % pageCount;
    if (!players.length) section.append(el('p', 'Rankings appear after the first completed match.', {class: 'court-idle'}));
    else {
        const table = el('table', '', {class: 'courtside-ranking-table'});
        const header = el('tr');
        for (const text of ['#', 'Player', 'W', 'L', 'Win %']) header.append(el('th', text, {scope: 'col'}));
        table.append(el('thead', '', {}, [header]));
        const body = el('tbody');
        for (const p of players.slice(index * 6, index * 6 + 6)) body.append(el('tr', '', {}, [
            leaderboardRank(p.rank), el('td', p.display_name), el('td', String(p.wins)),
            el('td', String(p.losses)), el('td', p.win_percentage.toFixed(0) + '%')
        ]));
        table.append(body);
        section.append(table);
    }
    section.append(el('small', 'All courts · Wins, win %, point difference' + (pageCount > 1 ? ' · Page ' + (index + 1) + '/' + pageCount : '')));
    return section;
}

function drawCourtside(data) {
    const board = el('section', '', {class: 'courtside-board' + (data.display?.show_header === false ? ' header-hidden' : '')}, [
        el('header', '', {class: 'courtside-heading'}, [
            el('div', '', {}, [el('h1', 'RALLY POINT'), el('p', 'COURTSIDE VIEW · '+clubDisplayName), el('p',data.event ? data.event.name+' · '+eventType(data.event.event_type) : 'No active event')]),
            badge('LIVE COURTS', true)
        ])
    ]);
    const courts = data.courts || [];
    board.append(el('div', '', {class: 'courtside-layout'}, [
        el('aside', '', {class: 'courtside-sidebar'}, [recommendedPanel(data), compactRanking(data.leaderboard)]),
        el('section', '', {class: 'courtside-scores' + (courts.length >= 3 ? ' compact-courts' : '') + (courts.length > 3 ? ' many-courts' : ''), 'aria-label': 'All court scores'},
            courts.length ? courts.map(courtsideCourt) : [el('p', 'No courts configured yet.', {class: 'empty'})])
    ]));
    document.querySelector('#courtside-display').replaceChildren(board);
    const headerToggle = document.querySelector('#preview-header-toggle');
    if (headerToggle) {
        const shown = data.display?.show_header !== false;
        headerToggle.textContent = shown ? 'HIDE TV HEADER' : 'SHOW TV HEADER';
        headerToggle.dataset.shown = String(shown);
        headerToggle.setAttribute('aria-pressed', String(!shown));
    }
    document.querySelector('#connection').textContent = '● LIVE';
}

async function loadCourtside() {
    if (params.get('display') === '1') document.body.classList.add('courtside-kiosk');
    let fetching = false;
    root.replaceChildren(title('COURTSIDE VIEW', 'Every court. Every player.', 'Live scores, the next recommended match, and club rankings.'),
        el('div', '', {class: 'row courtside-toolbar'}, [button('FULLSCREEN COURTSIDE VIEW', async () => {
            await document.querySelector('#courtside-display').requestFullscreen();
        }, 'primary'), el('p', 'Scores refresh automatically · Recommendations are confirmed by the operator', {class: 'muted'})]),
        el('div', '', {id: 'courtside-display'}, [el('p', 'Connecting to the courts…', {class: 'empty'})]));
    if (['ADMIN','OPERATOR'].includes(role)) {
        const headerToggle = button('HIDE TV HEADER',async()=>{
            const show_header=headerToggle.dataset.shown === 'false';
            await api('/api/preview-display',{show_header});
            await refresh();
            notify(show_header ? 'Preview header shown on this club’s TVs.' : 'Preview header hidden. Court panels now use the extra space.');
        });
        headerToggle.id='preview-header-toggle';
        document.querySelector('.courtside-toolbar').append(headerToggle);
    }
    const refresh = async () => {
        if (fetching) return;
        fetching = true;
        try {
            const data = await api('/api/courtside');
            drawCourtside(data);
            notify('');
        } catch (error) {
            document.querySelector('#connection').textContent = 'CONNECTION LOST';
            const mark = document.querySelector('.courtside-heading .badge');
            if (mark) { mark.textContent = 'OFFLINE · LAST UPDATE'; mark.classList.remove('live'); }
            notify('Connection lost. Showing the last received scores. ' + error.message);
        } finally { fetching = false; }
    };
    await refresh();
    setInterval(refresh, 1000);
}
async function loadControl() {
    const court = params.get('court');
    root.replaceChildren(title('REFEREE PANEL', 'Match control', 'Commands are confirmed by the HDMI scoring engine.'), el('div', '', {
        id: 'live-board'
    }));
    const d = await api('/api/live' + (court ? '?court=' + court : ''));
    drawLive(d);
    const controls = el('div', '', {
        class: 'control-grid'
    });
    const actions = [
        ['TEAM A POINT', 'TEAM_A_POINT'],
        ['TEAM B POINT', 'TEAM_B_POINT'],
        ['SIDE OUT / CHANGE SERVER', 'CHANGE_SERVER'],
        ['UNDO', 'TEAM_A_UNDO'],
        ['TEAM A TIMEOUT', 'TEAM_A_TIMEOUT'],
        ['TEAM B TIMEOUT', 'TEAM_B_TIMEOUT'],
        ['START NEXT GAME', 'NEXT_GAME'],
        ['END GAME', 'END_GAME'],
        ['PAUSE MATCH', 'PAUSE'],
        ['START READY MATCH / RESUME', 'RESUME'],
        ['END MATCH', 'END_MATCH'],
        ['RESET MATCH', 'RESET'],
        ['RESUME SAVED MATCH', 'RESUME_RECOVERY']
    ];
    for (const [label, name] of actions) controls.append(button(label, async () => {
        window.RallyScoreSounds?.arm();
        const danger = ['END_GAME', 'END_MATCH', 'RESET'].includes(name);
        if (danger && !confirm(label + '? This changes the active match.')) return;
        if (!latest?.match_id && name === 'END_MATCH' && document.body.dataset.hostAdmin === 'true') {
            const stopCourt = court || latest.identity?.court_id;
            if (!stopCourt) throw Error('Select a court before stopping Casual Play.');
            const stopped = await api('/api/courts/'+stopCourt+'/end-casual', {confirmed:true,expected_revision:latest.revision});
            await awaitReceipt(stopped,stopCourt);
            drawLive(await api('/api/live?court='+stopCourt));
            notify('Casual Play ended. This court can now receive a Club Managed match.');
            return;
        }
        if (!latest?.match_id) throw Error('Start a tracked match from web setup first');
        const r = await api('/api/matches/' + latest.match_id + '/commands', {
            name,
            confirmed: danger,
            request_id: commandId()
        });
        await awaitReceipt(r, court || '');
        if (['TEAM_A_POINT','TEAM_B_POINT','CHANGE_SERVER'].includes(name)) await window.RallyScoreSounds?.confirmed(r.request_id);
    }, ['END_GAME', 'END_MATCH', 'RESET'].includes(name) ? 'danger' : ''));
    const firstServe = button('CHANGE FIRST SERVE', async () => {
        if (!latest?.ready_to_play || !latest.match_id) throw Error('First serve can only change before starting the ready match.');
        const receipt = await api('/api/matches/' + latest.match_id + '/commands', {
            name:'CHANGE_FIRST_SERVE',request_id:commandId()
        });
        await awaitReceipt(receipt,court || '');
        drawLive(await api('/api/live' + (court ? '?court=' + court : '')));
    });
    firstServe.id = 'change-first-serve';
    firstServe.hidden = !d.ready_to_play;
    controls.append(firstServe);
    root.append(card('Controls', [controls]));
    const manual = el('form');
    const scores = el('div', '', {class: 'grid'}, [
        field('Team A score', 'score_a', d.state?.game?.scores.A ?? 0, null, 'number'),
        field('Team B score', 'score_b', d.state?.game?.scores.B ?? 0, null, 'number')
    ]);
    for (const input of scores.querySelectorAll('input')) {
        input.min = '0'; input.max = '999'; input.step = '1'; input.required = true;
    }
    const winner = field('Match winner', 'winner', '', [
        ['', 'Select winning team'],
        ['A', d.settings?.team_a || 'Team A'], ['B', d.settings?.team_b || 'Team B']
    ]);
    const useScores = field('Scores when declaring the winner', 'use_scores', 'keep', [
        ['keep', 'Keep the scores currently on the scoreboard'],
        ['edit', 'Use the manual scores entered above']
    ]);
    const sendManual = async name => {
        if (!latest?.match_id || latest.match_id !== d.match_id) throw Error('Match changed. Reload Controls before entering a result.');
        if (!latest.connected) throw Error('Scoreboard is offline. Reconnect before changing the result.');
        const values = formData(manual);
        const details = {};
        if (name === 'SET_SCORES' || values.use_scores === 'edit') {
            if (!manual.reportValidity()) return;
            details.scores = {A: Number(values.score_a), B: Number(values.score_b)};
        }
        if (name === 'DECLARE_WINNER') {
            if (!values.winner) throw Error('Select the winning team first');
            details.winner = values.winner;
        }
        details.expected_revision = latest.revision;
        const finalScores = details.scores || latest.state.game.scores;
        const promptText = name === 'DECLARE_WINNER'
            ? 'Declare ' + latest.settings['team_' + values.winner.toLowerCase()] + ' the MATCH WINNER with score ' + finalScores.A + ' – ' + finalScores.B + '? This finishes the entire match and updates player rankings.'
            : 'Set this game’s scores to ' + finalScores.A + ' – ' + finalScores.B + '?';
        if (!confirm(promptText)) return;
        const receipt = await api('/api/matches/' + latest.match_id + '/commands', {
            name, confirmed: true, payload: details, request_id: commandId()
        });
        await awaitReceipt(receipt, court || '');
    };
    manual.onsubmit = event => event.preventDefault();
    manual.append(scores, winner, useScores, el('div', '', {class: 'row'}, [
        button('SAVE MANUAL SCORES', () => sendManual('SET_SCORES')),
        button('DECLARE MATCH WINNER', () => sendManual('DECLARE_WINNER'), 'primary')
    ]));
    root.append(card('Manual score & winner', [
        el('p', 'Scores apply to the current game. Saving scores does not finish it automatically. Declare a winner to finish the entire match, including an early finish or forfeit. Undo restores the previous result.', {class: 'muted'}), manual
    ]));
    if (court) root.append(await phoneAdminPanel(court));
    startEvents(court);
}
async function loadCourts() {
    const d = await api('/api/dashboard');
    root.replaceChildren(title('FACILITY', 'Your courts', 'Assign matches, view activity, and manage availability.'), el('div', '', {
        class: 'grid'
    }, d.courts.map(c => {
        const cardNode = courtCard(c);
        cardNode.append(link('COURT PHONE VIEW', '/court/' + c.id), el('img', '', {src: '/api/courts/' + c.id + '/qr.svg', alt: 'Court ' + c.number + ' QR code', width: '180', height: '180', class: 'court-qr'}));
        if (role === 'ADMIN' || role === 'OPERATOR') cardNode.append(link('PHONE ACCESS', '/control?court=' + c.id));
        if (role === 'ADMIN' && !c.active_match_id) cardNode.append(button('CHANGE STATUS', async () => {
            const status = prompt('AVAILABLE, RESERVED, MAINTENANCE, or DISABLED', c.status);
            if (status) {
                await api('/api/courts/' + c.id, {
                    status
                });
                await loadCourts();
            }
        }));
        return cardNode;
    })));
    if (role === 'ADMIN') {
        const form = el('form', '', {}, [field('Court name', 'name'), field('Court number', 'number', '', null, 'number'), el('button', 'ADD COURT', {
            class: 'primary',
            type: 'submit'
        })]);
        form.onsubmit = async event => {
            event.preventDefault();
            try {
                await api('/api/courts', numberFields(formData(form), ['number']));
                await loadCourts();
            } catch (e) {
                notify(e.message);
            }
        };
        root.append(card('Create court', [form]));
    }
}
async function loadMatches() {
    root.replaceChildren(title('CLUB RECORDS', 'Match history', 'Results and an auditable timeline of every scoring action.'));
    if (params.get('id')) {
        const d = await api('/api/matches/' + params.get('id')),
            m = d.match;
        root.append(card('Match #' + m.id, [el('h3', names(m.settings_json).join(' vs ')), badge(m.status), el('p', 'Court ' + m.court_id + ' · ' + m.scoring_type + ' · ' + (modeLabels[m.matchmaking_mode] || m.matchmaking_mode || 'Legacy') + ' · ' + elapsed(m.started_at, m.ended_at) + ' elapsed'), el('p', 'Winner: ' + (m.winner_team ? m.settings_json['team_' + m.winner_team.toLowerCase()] : '—')), el('p', d.players.map(p => p.display_name + ' (' + p.team + ')').join(', ')), ...d.games.map(g => el('p', 'Game ' + g.game_number + ': ' + g.team_a_score + ' – ' + g.team_b_score))]), card('Timeline', [el('div', '', {
            class: 'timeline'
        }, d.timeline.map(e => el('p', elapsed(m.started_at, e.created_at) + ' · ' + e.event_type + ' · ' + (e.payload_json?.snapshot?.actor?.username || e.payload_json?.actor?.username || 'Court remote / system') + (e.payload_json?.reason || e.payload_json?.snapshot?.correction_reason ? ' · ' + (e.payload_json.reason || e.payload_json.snapshot.correction_reason) : ''))))]), link('ALL MATCHES', '/matches'));
        if (m.status === 'COMPLETE' && ['ADMIN', 'OPERATOR'].includes(role)) root.append(resultCorrection(m));
        return;
    }
    const filter = el('form'),
        grid = el('div', '', {
            class: 'grid'
        });
    for (const [k, label, type] of [
            ['date', 'Date', 'date'],
            ['player', 'Player ID', 'number'],
            ['court', 'Court ID', 'number']
        ]) grid.append(field(label, k, params.get(k) || '', null, type));
    const events = await api('/api/play-events');
    grid.append(field('Event','event',params.get('event') || '',[['','All events'],...events.events.map(e=>[e.id,e.name+' · '+(e.event_date || e.created_at.slice(0,10))])]));
    grid.append(field('Match type', 'type', params.get('type') || '', [
        ['', 'All'], 'Singles', 'Doubles'
    ]));
    filter.append(grid, el('button', 'FILTER', {
        type: 'submit'
    }));
    filter.onsubmit = e => {
        e.preventDefault();
        const data = formData(filter);
        for (const key of Object.keys(data))
            if (!data[key]) delete data[key];
        location.search = new URLSearchParams(data);
    };
    root.append(card('Filter results', [filter]));
    const matches = await api('/api/matches?' + params.toString());
    root.append(el('div', '', {
        class: 'grid'
    }, matches.map(m => card('#' + m.id + ' · ' + m.created_at.slice(0, 10), [el('p', names(m.settings_json).join(' vs ')), badge(m.status), el('p', 'Court ' + m.court_id + ' · ' + m.scoring_type + ' · ' + (modeLabels[m.matchmaking_mode] || m.matchmaking_mode || 'Legacy') + ' · ' + elapsed(m.started_at, m.ended_at) + ' elapsed'), el('p', 'Score ' + (m.state_json?.state?.game?.scores?.A ?? 0) + ' – ' + (m.state_json?.state?.game?.scores?.B ?? 0)), el('p', 'Winner ' + (m.winner_team ? m.settings_json['team_' + m.winner_team.toLowerCase()] : '—')), link('MATCH SUMMARY', '/matches?id=' + m.id)]))));
}
async function loadSettings() {
    const d = await api('/api/settings');
    root.replaceChildren(title('ADMINISTRATION', 'System Settings', 'Devices, staff and operational preferences.'), el('div','',{class:'row'},[link('Court Settings','/settings/courts'),link('Matchmaking Settings','/settings/matchmaking')]));
    const form = el('form'),
        grid = el('div', '', {
            class: 'grid'
        });
    const header = field('HDMI scoreboard header', 'scoreboard_header', d.options.scoreboard_header || 'RALLY POINT');
    header.querySelector('input').required = true; header.querySelector('input').maxLength = 100;
    grid.append(header, el('p','The active club name appears underneath on its assigned TVs. Changes sync automatically.',{class:'muted'}));
    grid.append(field('After match', 'after_match', d.options.after_match, ['RETURN_PLAYERS_TO_QUEUE', 'REMOVE_PLAYERS_FROM_QUEUE']));
    grid.append(link('Configure session matchmaking','/settings/matchmaking'));
    form.append(grid, el('button', 'SAVE SETTINGS', {
        class: 'primary',
        type: 'submit'
    }));
    form.onsubmit = async e => {
        e.preventDefault();
        const values = formData(form),
            weights = {...d.options.weights};
        try {
            await api('/api/settings', {
                scoreboard_header: values.scoreboard_header,
                after_match: values.after_match,
                weights
            });
            notify('Settings saved');
        } catch (err) {
            notify(err.message);
        }
    };
    root.replaceChildren(title('CLUB ADMINISTRATION', 'System settings', 'Transparent matchmaking and court/device assignments.'), card('Club policies', [form,link('ANNOUNCEMENT SETTINGS','/settings/announcements')]), card('Scoreboard devices', d.devices.map(dev => adminEditor(dev, 'devices'))));
    if (role === 'ADMIN') {
        const result = await fetch('/api/network-settings', {credentials:'same-origin'});
        if (result.ok) root.append(card('Host network settings', [el('p','Choose hostname or IP addresses for the host and paired clients.'),el('a','EDIT NETWORK SETTINGS',{href:'/network-settings',class:'button'})]));
    }
    const device = el('form', '', {}, [field('Device name', 'device_name'), field('Court database ID', 'court_id', '', null, 'number'), field('Hostname (unique per Pi)', 'hostname'), field('Management IP (optional)', 'ip_address'), field('Local IPC URL', 'bridge_url', 'http://127.0.0.1:8765'), el('button', 'REGISTER DEVICE', {
        type: 'submit'
    })]);
    device.onsubmit = async e => {
        e.preventDefault();
        try {
            await api('/api/devices', numberFields(formData(device), ['court_id']));
            await loadSettings();
        } catch (err) {
            notify(err.message);
        }
    };
    root.append(card('Add scoreboard device', [device]));
    const users = el('form', '', {}, [field('Username', 'username'), field('Password (12+ characters)', 'password', '', null, 'password'), field('Role', 'role', 'OPERATOR', ['ADMIN', 'OPERATOR', 'VIEWER']), el('button', 'CREATE STAFF ACCOUNT', {
        type: 'submit'
    })]);
    users.onsubmit = async e => {
        e.preventDefault();
        try {
            await api('/api/users', formData(users));
            users.reset();
            await loadSettings(); notify('Staff account created');
        } catch (err) {
            notify(err.message);
        }
    };
    root.append(card('Staff accounts', [...d.users.map(u => adminEditor(u, 'users')), users]));
    const audit = await api('/api/audit');
    root.append(el('div','',{class:'row'},[link('Court Settings','/settings/courts'),link('Matchmaking Settings','/settings/matchmaking')]));
    root.append(card('Operator audit', audit.slice(0, 30).map(r => el('p', r.created_at + ' · ' + r.actor + ' · ' + r.action + ' · ' + r.target + ' · ' + JSON.stringify(r.details)))));
}
function adminEditor(record, kind) {
    const device = kind === 'devices';
    const name = device ? record.device_name : record.username;
    const row = el('section', '', {class: 'card'}, [el('p', device ? '#' + record.id + ' · ' + name + ' · Court ' + record.court_id + ' · ' + record.status : name + ' · ' + record.role)]);
    if (device) row.append(link('DOWNLOAD PRIVATE DEVICE CONFIG', '/api/devices/' + record.id + '/configuration'));
    row.append(button('EDIT', async () => {
        const fields = device ? [field('Device name', 'device_name', name), field('Court database ID', 'court_id', record.court_id, null, 'number'), field('Local IPC URL', 'bridge_url', record.bridge_url)] : [field('Username', 'username', name), field('Role', 'role', record.role, ['ADMIN', 'OPERATOR', 'VIEWER']), field('New password (12+ characters; leave blank to keep current)', 'password', '', null, 'password')];
        const form = el('form', '', {}, [...fields, el('button', 'SAVE', {type: 'submit'}), button('CANCEL', loadSettings)]);
        form.onsubmit = async e => {
            e.preventDefault();
            try {
                const values = formData(form);
                if (device) { values.id = record.id; values.court_id = Number(values.court_id); }
                await api(device ? '/api/devices' : '/api/users/' + record.id, values);
                await loadSettings(); notify('Changes saved');
            } catch (err) { notify(err.message); }
        };
        row.replaceChildren(form);
    }), button('DELETE', async () => {
        if (!confirm('Delete ' + (device ? 'device registration' : 'staff account') + ' "' + name + '"? ' + (device ? 'The Raspberry Pi app stays installed.' : 'Their access will be revoked.'))) return;
        await api('/api/' + kind + '/' + record.id + '/delete', {});
        await loadSettings(); notify('Deleted');
    }, 'danger'));
    return row;
}
async function phoneAdminPanel(court) {
    const data = await api('/api/courts/' + court + '/phone/admin');
    const panel = card('Court phone scoring · ' + (data.enabled ? 'ENABLED' : 'DISABLED'), [el('p', 'View-only by default. Set a six-digit PIN, then approve each phone for one hour. Changing the PIN revokes existing access.')]);
    const form = el('form', '', {}, [field('Six-digit court PIN', 'pin', '', null, 'password'), el('button', 'ENABLE / CHANGE PIN', {type:'submit'})]);
    form.onsubmit = async event => {
        event.preventDefault();
        try { await api('/api/courts/' + court + '/phone/config', {enabled:true, pin:formData(form).pin}); await loadControl(); }
        catch (error) { notify(error.message); }
    };
    panel.append(form, button('DISABLE PHONE SCORING', async () => { await api('/api/courts/' + court + '/phone/config', {enabled:false}); await loadControl(); }));
    for (const grant of data.requests) panel.append(el('div', '', {class:'row'}, [el('span', grant.status + ' · Request ' + grant.id.slice(0, 6)), button(grant.status === 'PENDING' ? 'APPROVE' : 'REVOKE', async () => { await api('/api/courts/' + court + '/phone/approve', {id:grant.id, approved:grant.status === 'PENDING'}); await loadControl(); })]));
    panel.append(button('REFRESH REQUESTS', loadControl));
    return panel;
}
async function loadPhone() {
    const court = location.pathname.split('/').pop();
    root.replaceChildren(title('COURT ' + court, 'Court phone view', 'Watch live scores. Scoring requires the court PIN and staff approval.'), el('div', '', {id:'live-board'}), el('div', '', {id:'phone-controls'}));
    const render = async () => {
        const live = await api('/api/live?court=' + court);
        drawLive(live);
        const access = await api('/api/courts/' + court + '/phone');
        const container = document.querySelector('#phone-controls');
        // Preserve typed PIN while view-only; approved controls always use latest revision.
        if (container.dataset.status === access.status) return;
        container.dataset.status = access.status;
        container.replaceChildren(el('h2', access.status.replaceAll('_', ' ')));
        if (access.status === 'APPROVED') {
            for (const [label, name] of [['TEAM A POINT','TEAM_A_POINT'],['TEAM B POINT','TEAM_B_POINT'],['TEAM A LOST RALLY','TEAM_A_SERVICE'],['TEAM B LOST RALLY','TEAM_B_SERVICE'],['UNDO','TEAM_A_UNDO'],['TEAM A TIMEOUT','TEAM_A_TIMEOUT'],['TEAM B TIMEOUT','TEAM_B_TIMEOUT'],['RESUME','RESUME']]) container.append(button(label, async () => {
                const current = await api('/api/live?court=' + court);
                const receipt = await api('/api/courts/' + court + '/phone/commands', {name, revision:current.revision});
                for (let attempt=0; attempt<20; attempt++) {
                    const result = await api('/api/courts/' + court + '/phone/receipts/' + receipt.request_id);
                    if (result.status !== 'QUEUED') { if(result.error) throw Error(result.error); await render(); return; }
                    await new Promise(resolve => setTimeout(resolve, 150));
                }
                throw Error('Confirmation delayed. Check the score before trying again.');
            }));
        } else if (access.status === 'PENDING') container.append(el('p', 'Ask staff to approve request ' + access.request_code + ' on this court’s Controls page.'));
        else if (access.enabled) {
            const form = el('form', '', {}, [field('Court PIN', 'pin', '', null, 'password'), el('button','REQUEST SCORING ACCESS',{type:'submit'})]);
            form.onsubmit = async event => { event.preventDefault(); try { await api('/api/courts/' + court + '/phone/request', formData(form)); container.dataset.status=''; await render(); } catch(error) { notify(error.message); } };
            container.append(form);
        }
    };
    await render();
    setInterval(() => render().catch(error => notify(error.message)), 1500);
}
function resultCorrection(match) {
    const scores = match.state_json?.state?.completed_games?.at(-1) || {A:0,B:0};
    const form = el('form', '', {}, [field('Final game · Team A score','score_a',scores.A,null,'number'),field('Final game · Team B score','score_b',scores.B,null,'number'),field('Official match winner','winner',match.winner_team,['A','B']),field('Reason for correction','reason'),el('button','CORRECT OFFICIAL RESULT',{type:'submit'})]);
    form.onsubmit = async event => {
        event.preventDefault();
        if (!confirm('Correct this completed result? Rankings will be recalculated.')) return;
        try {
            const values = formData(form), live = await api('/api/live?court=' + match.court_id);
            const response = await api('/api/matches/' + match.id + '/correct', {scores:{A:Number(values.score_a),B:Number(values.score_b)},winner:values.winner,reason:values.reason,confirmed:true,revision:live.revision,record_revision:match.state_json?.revision || 0});
            if(response.request_id) await awaitReceipt(response,match.court_id);
            await loadMatches(); notify('Result corrected');
        } catch(error) { notify(error.message); }
    };
    return card('Correct completed result', [el('p','Changes the final game and official match winner. Previous games remain recorded. A reason and staff identity are recorded.'),form]);
}
async function boot() {
    switch (page) {
        case 'bluetooth':
            await loadBluetooth();
            break;
        case 'announcements':
            await loadAnnouncementSettings();
            break;
        case 'network-settings':
            await loadNetworkSettings();
            break;
        case 'court-manager':
            await loadCourtManager();
            break;
        case 'clubs':
            await loadClubs();
            break;
        case 'tournaments':
            await loadTournaments();
            break;
        case 'phone':
            await loadPhone();
            break;
        case 'dashboard':
            await loadDashboard();
            break;
        case 'queue':
            await loadQueue();
            break;
        case 'players':
            await loadPlayers();
            break;
        case 'checkin':
            await loadRotationCheckin();
            break;
        case 'setup':
            await loadSetup();
            break;
        case 'live':
            await loadLive();
            break;
        case 'preview':
            await loadCourtside();
            break;
        case 'leaderboard':
            leaderboardMode = true;
            root.replaceChildren(title('CLUB RANKINGS', 'Player leaderboard', 'Results from completed matches across all courts.'), el('div', '', {id: 'live-board'}));
            await leaderboardEventFilter();
            await refreshLeaderboard();
            leaderboardTimer = setInterval(() => refreshLeaderboard().catch(e => notify(e.message)), 5000);
            break;
        case 'control':
            await loadControl();
            break;
        case 'courts':
            await loadCourts();
            break;
        case 'play-events':
            await loadPlayEvents();
            break;
        case 'matches':
            await loadMatches();
            break;
        case 'rotation-courts':
            await loadRotationCourts(); break;
        case 'rotation-matchmaking':
            await loadRotationSettings(); break;
        case 'settings':
            await loadSettings();
            break;
    }
    if (['dashboard', 'queue', 'courts'].includes(page)) overviewTimer = setInterval(() => ({
        dashboard: loadDashboard,
        queue: loadQueue,
        courts: loadCourts
    } [page])().catch(e => notify(e.message)), 5000);
    if (['live', 'control'].includes(page)) setInterval(() => {
        if (latest) drawLive(latest);
    }, 1000);
}
boot().catch(e => {
    root.replaceChildren(el('p', 'Unable to load this page.', {
        class: 'empty'
    }));
    notify(e.message);
});
async function loadClubs() {
    const data = await api('/api/clubs');
    root.replaceChildren(title('SHARED CLUB SYSTEM', 'Choose your club', 'Separate rosters, staff, courts and results on this local server.'));
    for (const c of data.clubs) root.append(card(c.name, [el('p', c.public_live ? 'Public courtside preview' : 'Staff sign-in required'), link('OPEN CLUB', c.url)]));
    if (!data.can_create) { root.append(el('p','Sign in to the main club as a host administrator to add clubs.')); return; }
    const form = el('form', '', {}, [field('Club name','name'),field('Club URL slug (example: riverside)','slug'),field('First administrator username','username'),field('Administrator password (12+ characters)','password','',null,'password'),field('Public courtside preview','public_live','true',[['true','Yes'],['false','No — staff only']]),el('button','CREATE CLUB',{type:'submit'})]);
    form.onsubmit = async e => {e.preventDefault();try {const values=formData(form);values.public_live=values.public_live==='true';await api('/api/clubs',values);await loadClubs();notify('Club created. You can open its workspace directly as Host Administrator.');}catch(error){notify(error.message);}};
    root.append(card('Manage physical courts',[el('p','Move a TV and its RF remotes to another club without editing Pi configuration.'),link('MOVE COURTS BETWEEN CLUBS','/court-manager')]));
    root.append(card('Add a club', [form]));
}
function rosterImport() {
    const csv = el('textarea','',{rows:7,placeholder:'display_name,skill_rating,rating_type\nLarry,3.5,Manual\nBryan,4.0,DUPR'});
    const file = el('input','',{type:'file',accept:'.csv,text/csv'});
    file.onchange = async () => { if(file.files[0]) csv.value=await file.files[0].text(); };
    return card('Import club roster', [el('p','Upload or paste CSV. Names already in this club are skipped. All rows are checked before any player is added.'),file,csv,button('IMPORT PLAYERS',async()=>{const result=await api('/api/players/import',{csv:csv.value});await loadPlayers();notify(result.imported+' players added; '+result.skipped+' duplicate names skipped.');})]);
}
async function loadTournaments() {
    const [events,players,courts] = await Promise.all([api('/api/tournaments'),api('/api/players'),api('/api/courts')]);
    root.replaceChildren(title('CLUB TOURNAMENTS','Tournament events','Register fixed entries, generate fixtures, then assign each match to a court. Use manual matchmaking on tournament courts to avoid open-play assignments.'));
    const create=el('form','',{},[field('Tournament name','name'),field('Scoring','scoring_mode','Side-Out',['Side-Out','Rally']),field('Win by','win_by',2,[1,2]),field('Format','format','ROUND_ROBIN',[['ROUND_ROBIN','Round robin'],['SINGLE_ELIMINATION','Single elimination (2/4/8/16/32 entries)']]),field('Game type','game_type','Doubles',['Singles','Doubles']),field('Points to win','points_to_win',11,[11,15,21]),field('Match format','best_of',1,[[1,'Single game'],[3,'Best of 3'],[5,'Best of 5']]),el('button','CREATE TOURNAMENT',{type:'submit'})]);
    create.onsubmit=async e=>{e.preventDefault();try {await api('/api/tournaments',formData(create));await loadTournaments();}catch(error){notify(error.message);}};
    root.append(card('New event',[create]));
    for(const t of events) {
        const panel=card(t.name,[badge(t.status),el('p',t.settings_json.game_type+' · '+t.format.replaceAll('_',' '))]);
        if(t.status==='REGISTRATION') {
            const choices=players.map(p=>[p.id,p.display_name]);
            const entry=el('form','',{},[field('Entry/team name (optional)','name'),field('Player 1','p1','',choices),...(t.settings_json.game_type==='Doubles'?[field('Player 2','p2','',choices)]:[]),el('button','REGISTER ENTRY',{type:'submit'})]);
            entry.onsubmit=async e=>{e.preventDefault();try {const d=formData(entry);await api('/api/tournaments/'+t.id+'/entries',{name:d.name,players:[d.p1,...(d.p2?[d.p2]:[])]});await loadTournaments();}catch(error){notify(error.message);}};
            panel.append(entry,el('p',t.entries_json.map(e=>e.name).join(' · ')),button('GENERATE FIXTURES',async()=>{await api('/api/tournaments/'+t.id+'/generate',{});await loadTournaments();}));
        } else {
            const wins=t.entries_json.map((e,i)=>({name:e.name,wins:t.fixtures_json.filter(f=>f.winner===i).length})).sort((a,b)=>b.wins-a.wins);
            panel.append(el('p','Standings · '+wins.map(e=>e.name+' '+e.wins+' wins').join(' · ')));
            for(const f of t.fixtures_json) {
                const row=card('#'+f.id+' · '+(t.entries_json[f.a]?.name || 'Winner of match '+f.sources?.[0])+' vs '+(t.entries_json[f.b]?.name || 'Winner of match '+f.sources?.[1]),[badge(f.status),...(f.winner!==null&&f.winner!==undefined?[el('p','Winner: '+t.entries_json[f.winner].name)]:[])]);
                if(f.match_id) row.append(link('MATCH SETUP / START','/setup'),link('MATCH DETAILS','/matches?id='+f.match_id));
                else if(f.status==='READY' && t.status!=='NEEDS_REVIEW') { const court=field('Court','court_id','',courts.filter(c=>c.enabled&&c.status==='AVAILABLE').map(c=>[c.id,c.name]));row.append(el('p','Only online, available courts can be assigned. Configure a scoreboard device first if this list is empty.'),court,button('PREPARE MATCH',async()=>{await api('/api/tournaments/'+t.id+'/fixtures/'+f.id+'/assign',{court_id:Number(court.querySelector('select').value)});await loadTournaments();})); }
                panel.append(row);
            }
        }
        root.append(panel);
    }
    root.append(button('REFRESH RESULTS',loadTournaments));
}
async function loadCourtManager() {
    clearTimeout(courtManagerTimer);
    const data=await api('/api/court-manager');
    root.replaceChildren(title('HOST ADMINISTRATION','Move a court between clubs','Choose the physical TV, its destination club and court number. The system handles the Pi configuration. Match history stays in its original club.'));
    root.append(el('p','Host administrator override: confirming a move automatically ends the source court match, saves its scores, then transfers the court. Players can start again when the move is complete.',{class:'muted'}));
    root.append(el('a','NETWORK SETTINGS: HOSTNAME / IP ADDRESS',{href:'/network-settings',class:'button'}));
    for(const court of data.courts) {
        const moving=data.jobs.find(j=>j.physical_id===court.id&&['QUEUED','MOVING','NEEDS_REVIEW'].includes(j.status));
        const form=el('form','',{},[field('Destination club','target_slug',court.slug,data.clubs.map(c=>[c.slug,c.name])),field('Court number in destination club','court_number',court.court_number || court.id,null,'number')]);
        const panel=card(court.name,[badge(moving?'MOVING':(court.ready||court.can_override)?'ONLINE':'UNAVAILABLE',(court.ready||court.can_override)&&!moving),el('p','Currently: '+court.club_name+' · Court '+court.court_number),el('p',moving?moving.message:court.message),form]);
        const review=button('REVIEW MOVE',async()=>{
            const values=formData(form);
            const proposal=await api('/api/court-manager/plan?physical_id='+court.id+'&target_slug='+encodeURIComponent(values.target_slug)+'&court_number='+encodeURIComponent(values.court_number)+'&end_match=true');
            if(proposal.no_change){notify('This court is already assigned to that club and court number.');return;}
            const moveRequestId=commandId();
            const confirmation=card('Confirm court move',[el('h3',proposal.name+' → '+proposal.target_name+' · Court '+proposal.court_number),el('p',(proposal.will_end_match?'HOST OVERRIDE: The current match will be ended automatically before transfer. ':'')+'This moves the TV and RF remotes from '+proposal.source_name+'. Previous scores and players remain with that club. The destination court will be created automatically if needed.'),button(proposal.will_end_match?'END MATCH & TRANSFER':'CONFIRM MOVE',async()=>{await api('/api/court-manager/moves',{...proposal,request_id:moveRequestId,confirmed:true});await loadCourtManager();notify('Move queued. Keep the Pi powered on.');},'primary'),button('CANCEL',loadCourtManager)]);
            root.replaceChildren(title('REVIEW ASSIGNMENT','Ready to move this court?','Check the destination before confirming.'),confirmation);
        },'primary');
        review.disabled=!!moving||(!court.ready&&!court.can_override);panel.append(review);root.append(panel);
    }
    if(!data.courts.length)root.append(el('p','No physical courts have been paired with the host yet.'));
    if(data.jobs.length)root.append(card('Recent moves',data.jobs.slice(0,8).map(job=>el('section','',{class:'move-status'},[badge(({DONE:'COMPLETE',QUEUED:'WAITING',MOVING:'SWITCHING',FAILED:'MOVE FAILED',NEEDS_REVIEW:'RECOVERY NEEDED'})[job.status] || job.status,job.status==='DONE'),el('p',job.details.name+' → '+job.details.target_name+' · Court '+job.details.court_number),el('p',job.message),...(job.status==='DONE'?[link('OPEN DESTINATION CLUB',job.details.target_slug==='legacy'?'/':'/clubs/'+job.details.target_slug+'/')]:[]),...(job.status==='NEEDS_REVIEW'?[button('RETRY CONNECTION RECOVERY',async()=>{await api('/api/court-manager/moves/'+job.id+'/retry',{});await loadCourtManager();})]:[])]))));
    root.append(button('REFRESH STATUS',loadCourtManager),link('BACK TO CLUBS','/clubs'));
    clearTimeout(courtManagerTimer);
    if(data.jobs.some(j=>['QUEUED','MOVING'].includes(j.status)))courtManagerTimer=setTimeout(()=>loadCourtManager().catch(e=>notify(e.message)),1500);
}

async function loadNetworkSettings() {
    clearTimeout(networkSettingsTimer);
    const data = await api('/api/network-settings');
    if (!networkSettingsDraft && data.draft) networkSettingsDraft = data.draft;
    root.replaceChildren(title('HOST ADMINISTRATION', 'Network settings', 'Use a .local hostname or a private LAN IPv4 address. IP addresses should be reserved in your router.'));
    if (data.jobs.length) root.append(card('Latest connection result',[badge(data.jobs[0].status,data.jobs[0].status==='DONE'),el('p',data.jobs[0].message)]));
    root.append(card('Currently applied addresses',[el('p','Host: '+data.hub_address),...data.clients.map(c=>el('p',c.name+': '+c.address)),el('p','The fields below may contain your last saved draft. A successful Apply is required to change active connections.',{class:'muted'})]));
    const form = el('form', '', {}, [field('Host server address (clients connect here)', 'hub_address', networkSettingsDraft?.hub_address || data.hub_address)]);
    for (const client of data.clients) form.append(field(client.name + ' address (host connects here)', 'client_' + client.id, networkSettingsDraft?.clients?.[client.id] || client.address));
    form.append(el('p', 'CHECK CONNECTION saves your entered addresses as a draft and tests them. It does not change active connections. APPLY verifies and saves active settings, then reconnects idle courts. Finish or end every unfinished match first. Queued score updates are preserved and sync after reconnection.', {class:'muted'}));
    const submit = async mode => {
        const values = formData(form);
        const clients = Object.fromEntries(data.clients.map(c => [String(c.id), values['client_' + c.id]]));
        networkSettingsDraft = {hub_address:values.hub_address, clients};
        if (mode==='apply' && !confirm('Apply these connection addresses? All courts must be finished or ended. This reconnects the court devices.')) return;
        await api('/api/network-settings', {mode, ...networkSettingsDraft});
        await loadNetworkSettings();
    };
    const check = button('CHECK CONNECTION', () => submit('check'));
    const apply = button('APPLY NETWORK SETTINGS', () => submit('apply'), 'primary');
    form.append(el('div','',{class:'row'},[check,apply]));
    form.onsubmit = e => {e.preventDefault();};
    const pending = data.jobs.some(job => ['QUEUED','RUNNING'].includes(job.status));
    check.disabled = apply.disabled = pending;
    root.append(card('Paired court connections',[form]),el('a','BACK TO HOST ADMINISTRATION',{href:'/court-manager',class:'button'}));
    if (data.jobs.length) root.append(card('Connection checks and changes',data.jobs.map(job => el('section','',{},[badge(job.status,job.status==='DONE'),el('p',job.message)]))));
    if (pending) networkSettingsTimer = setTimeout(() => loadNetworkSettings().catch(e => notify(e.message)),2000);
}

async function loadAnnouncementSettings() {
    const service=window.RallyAnnouncements, current=service.settings;
    root.replaceChildren(title('OPERATOR PA SYSTEM','Announcement Settings','Sound plays on this computer through its selected audio output. Settings are saved for this browser and club.'));
    const form=el('form');
    const voices=field('Voice','voice',current.voice,[['','Automatic / language default']]);
    const language=field('Language','language',current.language,[['en','English'],['fil-PH','Filipino'],['en-PH','English (Philippines)']]);
    const updateVoices=()=>{
        const select=voices.querySelector('select'), previous=select.value || current.voice;
        select.replaceChildren(el('option','Automatic / language default',{value:''}));
        for(const voice of service.speech?.getVoices?.() || []) select.append(el('option',voice.name+' · '+voice.lang+(voice.localService?' · on-device':' · may need internet'),{value:voice.voiceURI}));
        select.value=previous;
        if(!select.value) select.value='';
        const langs=new Set([...language.querySelectorAll('option')].map(o=>o.value));
        for(const voice of service.speech?.getVoices?.() || []) if(!langs.has(voice.lang)) {language.querySelector('select').append(el('option',voice.lang,{value:voice.lang}));langs.add(voice.lang);}
        language.querySelector('select').value=current.language;
    };
    updateVoices();service.speech?.addEventListener?.('voiceschanged',updateVoices);
    form.append(el('div','',{class:'grid'},[
        field('Voice Announcements','enabled',String(current.enabled),[['true','Enabled'],['false','Disabled']]),
        field('Automatically announce new assigned matches','auto',String(current.auto),[['false','Disabled'],['true','Enabled — keep Dashboard open']]),
        voices,language,field('Volume (0–100%)','volume',current.volume*100,null,'number'),
        field('Speech rate (0.5–2.0)','rate',current.rate,null,'number'),field('Pitch (0–2)','pitch',current.pitch,null,'number')
    ]));
    for(const [name,min,max,step] of [['volume',0,100,1],['rate',.5,2,.1],['pitch',0,2,.1]]) Object.assign(form.elements[name],{min,max,step});
    const template=el('textarea','',{name:'template',rows:'4',maxlength:'2000'});template.value=current.template;
    form.append(el('label','Announcement template',{},[template]),el('p','Placeholders: {court}, {team_a}, {team_b}, {team_a_player_1}, {team_a_player_2}, {team_b_player_1}, {team_b_player_2}. Use {team_a} / {team_b} for natural singles and doubles names.',{class:'muted'}));
    const read=()=>{const data=formData(form);if(!form.reportValidity()) throw Error('Check the volume, rate and pitch values.');return {...data,enabled:data.enabled==='true',auto:data.auto==='true',volume:Number(data.volume)/100,rate:Number(data.rate),pitch:Number(data.pitch)};};
    const save=()=>{service.save(read());service.refreshIndicator?.();};
    form.onsubmit=e=>{e.preventDefault();try{save();notify('Announcement settings saved on this browser');}catch(error){notify(error.message);}};
    form.append(el('div','',{class:'row'},[el('button','SAVE ANNOUNCEMENT SETTINGS',{type:'submit',class:'primary'}),button('TEST VOICE',()=>{save();service.arm();service.enqueue({court_name:'Sample Court',teams:{A:[{name:'Alex'},{name:'Sam'}],B:[{name:'Jamie'},{name:'Taylor'}]}},'TEST');})]));
    root.append(card('Operator computer audio',[form,el('p','Connect this computer to your PA speakers. Use Test Voice to enable browser audio. Choose an on-device voice for offline use. Muting stops this browser’s queue without affecting matches.',{class:'muted'})]));
    root.append(await scoreSoundSettingsCard());
    const previewSettings = await api('/api/announcements/preview-voice');
    const previewVoice = field('Preview Pi voice', 'preview_voice', previewSettings.voice, [['female','Female · Lessac'],['male','Male · Ryan']]);
    root.append(card('Preview Pi speaker · Offline Piper voice', [previewVoice,
        el('p','This club’s choice applies to the next announcement on its Preview Pi. The browser voice above is separate. Both Piper voices work offline after installation.',{class:'muted'}),
        button('SAVE PREVIEW VOICE',async()=>{
            const voice=previewVoice.querySelector('select').value;
            await api('/api/announcements/preview-voice',{voice});
            notify('Preview Pi voice saved. Use Replay on Preview Speaker to hear it.');
        },'primary')]));
    try {const events=await api('/api/announcements/events');root.append(card('Recent announcement reports',events.map(event=>el('p',event.announced_at+' · Match #'+event.match_id+' · Court ID '+event.court_id+' · '+event.announcement_type+' · '+event.playback_status+' · '+event.operator))));} catch(_){notify('Announcement reports unavailable; voice settings still work.');}
}

// Keep the navigation usable with mouse, touch and keyboard.
document.addEventListener('click', event => {
    const menu=document.querySelector('.settings-menu');
    if(menu && !menu.contains(event.target)) menu.open=false;
});
document.addEventListener('keydown', event => {
    if(event.key!=='Escape') return;
    const menu=document.querySelector('.settings-menu');
    if(menu?.open){menu.open=false;menu.querySelector('summary').focus();}
});

function eventType(value) { return value === 'TOURNAMENT' ? 'Tournament' : 'Open Play'; }
async function loadPlayEvents() {
    const data = await api('/api/play-events');
    root.replaceChildren(title('CLUB EVENTS','Open Play & Tournaments','Start a new event or resume saved check-in, queue and results. Player profiles and past matches are preserved.'));
    if(data.active) {
        const event=data.active;
        root.append(card('Active event: '+event.name,[
            el('p',eventType(event.event_type)+' · '+(event.event_date || event.created_at.slice(0,10))),
            link('CHECK IN PLAYERS','/checkin'),link('EVENT LEADERBOARD','/leaderboard?event='+event.id),
            ...(event.event_type === 'TOURNAMENT' ? [link('TOURNAMENT BRACKETS & SCHEDULE','/tournaments')] : [link('WAITING QUEUE','/queue')]),
            button('END EVENT',async()=>{
                if(!confirm('End '+event.name+'? Check-in and queue will be saved for Resume Event, then cleared. Unsent match drafts will be cancelled. Results and player profiles will be retained. Assigned matches must be finished first.'))return;
                await api('/api/play-events/'+event.id+'/close',{confirmed:true});
                recommendationDraft=null;await loadPlayEvents();
            },'danger')
        ]));
    } else {
        const now=new Date(),day=now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0')+'-'+String(now.getDate()).padStart(2,'0');
        const form=el('form','',{},[
            field('Event name','name','Open Play · '+day),
            field('Event type','event_type','OPEN_PLAY',[['OPEN_PLAY','Open Play'],['TOURNAMENT','Tournament']]),
            field('Event date','event_date',day,null,'date'),
            el('p','Starting an event clears any remaining check-in and queue. Players must check in again. Past results are archived.',{class:'muted'}),
            el('button','START EVENT',{type:'submit',class:'primary'})
        ]);
        form.querySelector('[name="name"]').required=true;
        form.onsubmit=async e=>{
            e.preventDefault();if(!confirm('Start a new event with fresh check-in and rankings?'))return;
            const submit=form.querySelector('button[type="submit"]');submit.disabled=true;
            try {const event=await api('/api/play-events',{...formData(form),confirmed:true});recommendationDraft=null;notify('Event started: '+event.name);await loadPlayEvents();}
            catch(error){notify(error.message);submit.disabled=false;}
        };
        root.append(card('New event',[form]));
    }
    root.append(card('Event history', data.events.length ? data.events.map(e=>el('div','',{class:'queue-row'},[
        el('div','',{},[el('strong',e.name),el('p',eventType(e.event_type)+' · '+(e.event_date || e.created_at.slice(0,10))+' · '+(e.active ? 'ACTIVE' : 'CLOSED'))]),
        eventHistoryActions(e, data.active)
    ])) : [el('p','No events yet.')]));
}
async function leaderboardEventFilter() {
    if(!['ADMIN','OPERATOR','VIEWER'].includes(role))return;
    const data=await api('/api/play-events');
    const selected=params.get('scope')==='all' ? 'all' : (params.get('event') || '');
    const selector=field('Ranking period','ranking-period',selected,[['','Active event'],['all','All-time'],...data.events.map(e=>[e.id,e.name+' · '+(e.event_date || e.created_at.slice(0,10))])]);
    selector.querySelector('select').onchange=async e=>{
        params.delete('scope');params.delete('event');
        if(e.target.value==='all')params.set('scope','all');else if(e.target.value)params.set('event',e.target.value);
        history.replaceState(null,'',workspacePath('/leaderboard')+'?'+params.toString());await refreshLeaderboard();
    };
    root.insertBefore(selector,document.querySelector('#live-board'));
}

function eventHistoryActions(event, active) {
    const actions = el('div','',{class:'row'},[
        link('RESULTS','/leaderboard?event='+event.id),
        link('MATCH HISTORY','/matches?event='+event.id)
    ]);
    if (!event.active) {
        const resume = button('RESUME EVENT',async()=>{
            const day = event.event_date || event.created_at.slice(0,10);
            const queueMessage = event.resume_queue_available ? 'Saved checked-in players and queue will be restored.' : 'This older event has no saved queue; players will need to check in again.';
            if (!confirm('Resume '+event.name+' ('+day+')? Existing results will be kept. '+queueMessage+' Completed matches will not restart.')) return;
            const resumed = await api('/api/play-events/'+event.id+'/resume',{confirmed:true});
            recommendationDraft=null;
            expandedRecommendations.clear();
            await loadPlayEvents();
            notify(resumed.queue_snapshot_available
                ? 'Event resumed. Restored '+resumed.restored_players+' checked-in players and '+resumed.restored_queue+' queue entries.'
                : 'Event resumed. This older event has no saved queue snapshot; please check in players again.');
        },'primary');
        resume.disabled=Boolean(active);
        if(active) resume.title='End the current active event before resuming this event';
        actions.append(resume);
        if(active) actions.append(el('small','End the current event to resume this one.'));
    }
    return actions;
}


async function loadBluetooth() {
    root.replaceChildren(title('HOST AUDIO & DEVICES', 'Bluetooth', 'Manage Bluetooth devices connected to the Raspberry Pi host.'),
        card('Which computer is controlled?', [el('p', 'These controls operate the host Pi shared by all clubs. They do not control Bluetooth on your phone, laptop, or the separate Preview Pi.'),
            el('p', 'Using a laptop connected to the TV? Pair the speaker in the laptop’s Bluetooth settings and enable announcements in the operator browser.', {class:'muted'})]),
        el('div','',{id:'bluetooth-panel'}));
    let refreshing = false;
    let submitting = false;
    async function act(action, address='') {
        if (submitting) return;
        if (['disconnect','forget'].includes(action) && !confirm((action==='forget' ? 'Forget this device? Pairing will be required again.' : 'Disconnect this device? Any host audio using it will stop.') + ' This affects the shared host for all clubs.')) return;
        submitting = true;
        document.querySelectorAll('#bluetooth-panel button').forEach(b=>b.disabled=true);
        try {
            await api('/api/bluetooth', {action,address});
            notify('Bluetooth request accepted. Please wait for the result below.');
        } catch (error) { notify(error.message); }
        finally { submitting=false; await refresh(); }
    }
    async function refresh() {
        if (refreshing || submitting || document.hidden) return;
        refreshing=true;
        try {
            const data=await api('/api/bluetooth');
            const panel=document.querySelector('#bluetooth-panel');
            const state=card('Host: '+(data.hostname || 'Raspberry Pi'),[
                badge(data.available ? (data.adapter.powered ? 'BLUETOOTH ON' : 'BLUETOOTH OFF') : 'BLUETOOTH OFFLINE', data.available && data.adapter.powered),
                el('p',data.error || (data.busy ? 'Working. Scanning and pairing can take 15–25 seconds.' : 'Put your speaker/device in pairing mode, scan, then select Pair and Connect.')),
                el('div','',{class:'row'},[button('ENABLE BLUETOOTH',()=>act('enable'),'primary'),button('SCAN FOR DEVICES',()=>act('scan')),button('REFRESH',refresh)])
            ]);
            if(data.job) state.append(el('p', data.job.status+' · '+data.job.message, {class:'bluetooth-result',role:'status'}));
            const rows=data.devices.map(device=>el('div','',{class:'bluetooth-device'},[
                el('div','',{},[el('strong',device.name),el('p',device.address+' · '+(device.connected ? 'CONNECTED' : device.paired ? 'PAIRED' : 'NOT PAIRED'),{class:'muted'})]),
                el('div','',{class:'row'},[
                    ...(!device.paired ? [button('PAIR',()=>act('pair',device.address))] : []),
                    button(device.connected ? 'DISCONNECT' : 'CONNECT',()=>act(device.connected ? 'disconnect' : 'connect',device.address),device.connected ? '' : 'primary'),
                    ...(device.paired ? [button('FORGET',()=>act('forget',device.address),'danger')] : [])
                ])
            ]));
            panel.replaceChildren(state,card('Devices',rows.length ? rows : [el('p','No devices found. Enable Bluetooth and scan while the device is in pairing mode.')]),
                card('Pairing help',[el('p','Speakers and headsets that pair without a PIN are supported here. Devices requiring a PIN or passkey confirmation must be paired from the host desktop Bluetooth panel.'),
                    el('p','A speaker already connected to another computer may need to be disconnected there first. Connecting a device here does not move audio from the Preview Pi or laptop.',{class:'muted'})]));
            if(data.busy) panel.querySelectorAll('button').forEach(b=>b.disabled=true);
        } catch(error) { notify(error.message); }
        finally { refreshing=false; }
    }
    await refresh();
    const timer=setInterval(refresh,2000);
    window.addEventListener('pagehide',()=>clearInterval(timer),{once:true});
}


async function scoreSoundSettingsCard() {
    const settings=await api('/api/score-sounds');
    const select=field('Scoring beep output','score_sound_output',settings.output,[
        ['both','Both · Preview speaker + operator device'],
        ['preview','Preview Pi speaker only'],
        ['operator','Operator laptop / phone only'],
        ['off','Off']
    ]);
    return card('Scoring sound',[select,el('p','Preview beeps follow accepted RF and Controls scoring events. Operator beeps play after confirmed Controls actions on this device. These settings do not change the court Pi’s own sound setting.',{class:'muted'}),
        el('div','',{class:'row'},[
            button('SAVE SOUND OUTPUT',async()=>{
                await api('/api/score-sounds',{output:select.querySelector('select').value});
                notify('Scoring sound output saved for this club.');
            },'primary'),
            button('TEST THIS DEVICE',async()=>{
                if(!await window.RallyScoreSounds?.test())notify('Tap a scoring button to enable audio, and check this device’s volume.');
            })
        ])]);
}
