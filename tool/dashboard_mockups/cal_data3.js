// Shared data for the minimalist (light, dashboard-matched) BD Calendar set.
// Click a month -> findings grouped under the three trigger categories.
const MON  = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const FULL = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const NOW = 4, FRESH = 5;
const TYPE_ORDER = ['reg','pol','evt'];
const TYPE_LABEL = { reg:'Regulatory deadline', pol:'Policy timeline', evt:'Comms event' };
const t = type => ({ type });

const MAY = [
  { type:'reg', name:'FCA Consumer Duty — annual board-report ramp', when:'71d',
    seat:'Head of Regulatory / Customer Communications',
    angle:'Q2 run-up to the 31 Jul board report — a repeatable retained-search window.' },
  { type:'reg', name:'UK annual-report & AGM season', when:'40d',
    seat:'Head of Investor Relations / Corporate-Reporting Comms',
    angle:'Dec year-end issuers report Feb–Apr and run AGMs Apr–Jun — pitch before the crunch.' },
  { type:'pol', name:'UK SRS — first sustainability-reporting build-up', when:'2026',
    seat:'Head of Sustainability / ESG Communications',
    angle:'Issuers staff up ahead of the first mandatory cycle — secure the retained brief early.' },
  { type:'pol', name:'Machinery-of-government — post-Spending-Review reshuffle', when:'2026/27',
    seat:'Director of Communications (GCS) — transition comms',
    angle:'Spending-Review settlements drive departmental restructures and GCS comms briefs.' },
  { type:'evt', name:'PRWeek Strategic Internal Comms Conference', when:'22d',
    seat:'Internal-comms event · London',
    angle:'Senior in-house IC leaders convene — a cue to warm target accounts before Q3.' },
];

const DATA = [
  [t('reg'),t('pol')], [t('pol')], [t('reg'),t('pol'),t('evt')], [t('reg'),t('evt')],
  MAY,
  [t('reg'),t('pol'),t('evt'),t('reg'),t('pol')], [t('reg'),t('reg'),t('pol'),t('evt')],
  [t('pol'),t('evt')], [t('reg'),t('pol'),t('evt')],
  [t('reg'),t('pol'),t('evt'),t('reg'),t('pol'),t('evt'),t('reg')], [t('evt')], [t('reg'),t('pol'),t('evt')],
];

const POOL = {
  reg:[['PRA remuneration disclosure window','55d','Head of Regulatory Communications'],
       ['Solvency UK reporting cycle','88d','Head of Corporate-Reporting Comms'],
       ['Listing Rules governance refresh','120d','Head of Governance Communications']],
  pol:[['Audit reform (ARGA) transition','2026','Head of Corporate Affairs'],
       ['Pensions dashboards rollout','2026','Head of Policy Communications'],
       ['Procurement Act go-live comms','2026/27','Head of Change Communications']],
  evt:[['CIPR National Conference','34d','External-comms event · Manchester'],
       ['IoIC Festival of Internal Comms','61d','Internal-comms event · Birmingham'],
       ['Corporate Affairs Summit','78d','Corporate-affairs event · London']],
};
function expand(m){
  if (m === NOW) return MAY;
  const cnt = { reg:0, pol:0, evt:0 };
  return DATA[m].map(f => {
    const p = POOL[f.type][cnt[f.type]++ % POOL[f.type].length];
    return { type:f.type, name:p[0], when:p[1], seat:p[2],
             angle:'Dated trigger building demand for the seat in scope.' };
  });
}
function esc(s){ return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

// Grouped detail: one minimal section per present category.
function groupedHTML(m){
  const items = expand(m);
  let html = '';
  TYPE_ORDER.forEach(ty => {
    const rows = items.filter(i => i.type === ty);
    if (!rows.length) return;
    html += '<div class="grp">'+
      '<div class="grp-h '+ty+'"><span class="gd"></span>'+esc(TYPE_LABEL[ty])+'<span class="gc">'+rows.length+'</span></div>'+
      rows.map(r =>
        '<div class="li">'+
          '<div class="li-top"><span class="li-name">'+esc(r.name)+'</span>'+
            (r.when?'<span class="li-when">'+esc(r.when)+'</span>':'')+'</div>'+
          (r.seat?'<div class="li-seat">'+esc(r.seat)+'</div>':'')+
          (r.angle?'<div class="li-angle">'+esc(r.angle)+'</div>':'')+
        '</div>').join('')+
      '</div>';
  });
  return html;
}
function counts(m){ const c={reg:0,pol:0,evt:0}; DATA[m].forEach(f=>c[f.type]++); return c; }
