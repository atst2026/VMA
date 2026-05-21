// Shared sample data + detail-card renderer for the BD Calendar mockups.
// Pip TYPES are now three parallel trigger categories:
//   reg = Regulatory deadline (fixed statutory / regulator date)
//   pol = Policy timeline     (policy timeline still firming)
//   evt = Comms event         (industry comms event)
const MON  = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const FULL = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const NOW = 4, FRESH = 5;            // May = current month, Jun = new this week
const TYPE_LABEL = { reg:'Regulatory deadline', pol:'Policy timeline', evt:'Comms event' };

function t(type){ return { type }; }  // type-only finding (drives the tick)

const MAY = [
  { type:'reg', name:'FCA Consumer Duty — annual board-report ramp', when:'71d left',
    seat:'Head of Regulatory / Customer Communications',
    angle:'FS firms must lay an annual Consumer Duty board report by 31 Jul; the Q2 run-up is a repeatable retained-search window — pitch before they advertise.',
    targets:['Lloyds','Aviva','Schroders','L&G','NatWest','M&G'],
    scope:'FCA-regulated retail financial-services firms' },
  { type:'pol', name:'UK SRS — first sustainability-reporting build-up', when:'2026 cycle',
    seat:'Head of Sustainability / ESG & Corporate-Reporting Communications',
    angle:'Large issuers build sustainability-reporting comms ahead of the first mandatory cycle — get the retained brief before the rush hire.',
    targets:['Shell','BP','GSK','Unilever'],
    scope:'FTSE-weight UK-listed issuers (financials, energy, pharma, industrials)' },
  { type:'evt', name:'PRWeek Strategic Internal Comms Conference', when:'in 22d',
    seat:'Internal-comms event · London',
    angle:'Senior in-house IC leaders convene — a timing cue to warm target accounts before the Q3 hiring run.',
    targets:[],
    scope:'London · Internal comms' },
];

const DATA = [
  [t('reg'),t('pol')],                                   // Jan
  [t('pol')],                                            // Feb
  [t('reg'),t('pol'),t('evt')],                          // Mar
  [t('reg'),t('evt')],                                   // Apr
  MAY,                                                   // May (selected)
  [t('reg'),t('pol'),t('evt'),t('reg'),t('pol')],        // Jun (new)
  [t('reg'),t('reg'),t('pol'),t('evt')],                 // Jul
  [t('pol'),t('evt')],                                   // Aug
  [t('reg'),t('pol'),t('evt')],                          // Sep
  [t('reg'),t('pol'),t('evt'),t('reg'),t('pol')],        // Oct
  [t('evt')],                                            // Nov
  [t('reg'),t('pol'),t('evt')],                          // Dec
];

function esc(s){ return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

// Renders one finding as a detail row. Missing fields degrade gracefully.
function itemHTML(p){
  const targets = (p.targets||[]).length
    ? '<div class="d-tg">'+p.targets.map(x=>'<span>'+esc(x)+'</span>').join('')+'</div>' : '';
  return ''+
    '<div class="d-item">'+
      '<div class="d-head">'+
        '<span class="chip '+p.type+'">'+esc(TYPE_LABEL[p.type])+'</span>'+
        '<span class="d-name">'+esc(p.name||'—')+'</span>'+
        (p.when?'<span class="d-when">'+esc(p.when)+'</span>':'')+
        '<button class="d-rm">Remove</button>'+
      '</div>'+
      (p.seat?'<div class="d-seat">'+esc(p.seat)+'</div>':'')+
      (p.angle?'<div class="d-angle">'+esc(p.angle)+'</div>':'')+
      targets+
      (p.scope?'<div class="d-foot">'+esc(p.scope)+' · <a href="#">source</a></div>':'')+
    '</div>';
}
