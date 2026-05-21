// Shared data for the AI-instrument BD Calendar mockups (fixed 400px panel).
// Three parallel trigger categories:
//   reg = Regulatory deadline | pol = Policy timeline | evt = Comms event
const MON  = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const FULL = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const NOW = 4, FRESH = 5;
const TYPE_LABEL = { reg:'Regulatory deadline', pol:'Policy timeline', evt:'Comms event' };
const t = type => ({ type });

// Selected month carries 5 full windows so the detail overflows the fixed
// panel and demonstrates the internal scroll (panel never grows).
const MAY = [
  { type:'reg', name:'FCA Consumer Duty — annual board-report ramp', when:'71d left',
    seat:'Head of Regulatory / Customer Communications',
    angle:'FS firms must lay an annual Consumer Duty board report by 31 Jul; the Q2 run-up is a repeatable retained-search window — pitch before they advertise.',
    targets:['Lloyds','Aviva','Schroders','L&G','NatWest','M&G'],
    scope:'FCA-regulated retail financial-services firms' },
  { type:'reg', name:'UK annual-report & AGM season', when:'40d left',
    seat:'Head of Investor Relations / Corporate-Reporting & Governance Communications',
    angle:'Dec-year-end issuers publish annual reports Feb–Apr and run AGMs Apr–Jun; the run-up is a repeatable retained-search window — pitch before the crunch.',
    targets:['Tesco','Diageo','BT','Rolls-Royce'],
    scope:'FTSE-weight Dec-year-end UK-listed issuers' },
  { type:'pol', name:'UK SRS — first sustainability-reporting build-up', when:'2026 cycle',
    seat:'Head of Sustainability / ESG & Corporate-Reporting Communications',
    angle:'Large issuers build sustainability-reporting comms ahead of the first mandatory cycle — get the retained brief before the rush hire.',
    targets:['Shell','BP','GSK','Unilever'],
    scope:'FTSE-weight UK-listed issuers (financials, energy, pharma, industrials)' },
  { type:'pol', name:'Machinery-of-government — post-Spending-Review reshuffle', when:'2026/27',
    seat:'Director of Communications (GCS) — transition & change communications',
    angle:'Spending Review settlements drive 2026/27 departmental restructures; GCS comms-leadership briefs open as departments reorganise — be ahead of the cycle.',
    targets:['Cabinet Office','DWP','DHSC','HMRC'],
    scope:'UK central-government departments and major ALBs' },
  { type:'evt', name:'PRWeek Strategic Internal Comms Conference', when:'in 22d',
    seat:'Internal-comms event · London',
    angle:'Senior in-house IC leaders convene — a timing cue to warm target accounts before the Q3 hiring run.',
    targets:[],
    scope:'London · Internal comms' },
];

const DATA = [
  [t('reg'),t('pol')], [t('pol')], [t('reg'),t('pol'),t('evt')], [t('reg'),t('evt')],
  MAY,
  [t('reg'),t('pol'),t('evt'),t('reg'),t('pol')], [t('reg'),t('reg'),t('pol'),t('evt')],
  [t('pol'),t('evt')], [t('reg'),t('pol'),t('evt')],
  [t('reg'),t('pol'),t('evt'),t('reg'),t('pol'),t('evt'),t('reg')], [t('evt')], [t('reg'),t('pol'),t('evt')],
];

function esc(s){ return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function itemHTML(p){
  const targets = (p.targets||[]).length
    ? '<div class="d-tg">'+p.targets.map(x=>'<span>'+esc(x)+'</span>').join('')+'</div>' : '';
  return ''+
    '<div class="d-item">'+
      '<div class="d-head">'+
        '<span class="chip '+p.type+'">'+esc(TYPE_LABEL[p.type])+'</span>'+
        (p.when?'<span class="d-when">'+esc(p.when)+'</span>':'')+
        '<button class="d-rm">remove</button>'+
      '</div>'+
      '<div class="d-name">'+esc(p.name||'—')+'</div>'+
      (p.seat?'<div class="d-seat">'+esc(p.seat)+'</div>':'')+
      (p.angle?'<div class="d-angle">'+esc(p.angle)+'</div>':'')+
      targets+
      (p.scope?'<div class="d-foot">'+esc(p.scope)+' · <a href="#">source</a></div>':'')+
    '</div>';
}
function detailHTML(m){
  const ps=DATA[m];
  return '<div class="d-cap"><span class="mm">'+FULL[m]+'</span>'+
         '<span class="sub">'+ps.length+' window'+(ps.length!==1?'s':'')+'</span></div>'+
         ps.map(itemHTML).join('');
}
