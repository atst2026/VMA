"""Business Development Engine — the live site's single-page UI.

This replaces the landing page, the icon rail and the three dashboard pages
with one engine-shaped page (the signed-off concept 18):

  header        VMA tile · title · Communications/Marketing desk switch
  desk row      LEADS tile · BUILD-A-DECK sticky note · pinned wall calendar
  the engine    live funnel numbers + trigger ticker
  the board     Warm Signals (mr_bd) / Live Jobs (mr_jobs) with the
                deal-sheet portfolio per lead, filters and sort
  calendar      placement windows · frameworks · events (live pulses)
  workshop      the Personal Assistant composer, verbatim behaviour

Data contract: the SAME context _render_dashboard() builds for the legacy
TEMPLATE (mr_bd / mr_jobs / mr_meta / framework_events / example_role …)
plus the engine extras assembled in dashboard.py (eng_counts, eng_triggers,
eng_jobboards, eng_events, eng_pulses, eng_months, desk_key).
All actions hit the existing /api/* endpoints — nothing server-side moved.
"""

ENGINE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VMA — Business Development Engine</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&family=Space+Grotesk:wght@500;600;700&family=Newsreader:wght@400;500&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --ink:#101626;--ink2:#3C4043;--muted:#5A6577;--dim:#9AA0A6;
  --vma:#3E5C84;--deep:#1A3D7C;--blue:#4285F4;--wash:#E8F0FE;--clay:#D97757;
  --grn:#1E7A41;--amb:#B45309;--red:#C0392B;--teal:#0e7c74;--viol:#6b3fb5;
  --hair:rgba(16,22,38,.09);--border:rgba(60,64,67,.18);
  --mono:'JetBrains Mono',ui-monospace,monospace;--disp:'Space Grotesk','Inter',sans-serif;
}
body{font-family:'Inter',-apple-system,'Segoe UI',sans-serif;color:var(--ink);
  background-color:rgb(250,251,254);-webkit-font-smoothing:antialiased;letter-spacing:-.005em;overflow-x:hidden;
  /* the rail owns the left edge; everything else centres in what remains,
     so every view (the workshop especially) sits symmetrically */
  padding-left:68px}
.amb{position:fixed;z-index:-1;border-radius:50%;filter:blur(80px);pointer-events:none}
.amb.a1{width:1300px;height:950px;top:38%;left:50%;transform:translate(-50%,-50%);
  background:radial-gradient(closest-side,rgba(125,190,255,.62),rgba(157,210,255,.36) 50%,transparent 78%);
  animation:drift1 36s ease-in-out infinite alternate}
.amb.a2{width:700px;height:600px;right:-160px;top:-120px;
  background:radial-gradient(closest-side,rgba(217,119,87,.16),transparent 75%);animation:drift2 44s ease-in-out infinite alternate}
.amb.a3{width:640px;height:560px;left:-180px;bottom:-140px;
  background:radial-gradient(closest-side,rgba(62,92,132,.2),transparent 75%);animation:drift1 50s ease-in-out infinite alternate-reverse}
@keyframes drift1{to{transform:translate(-46%,-52%) scale(1.05)}}
@keyframes drift2{to{transform:translate(-40px,30px)}}
.glass{background:linear-gradient(135deg,rgba(255,255,255,.6),rgba(255,255,255,.3));
  backdrop-filter:blur(28px) saturate(1.8);-webkit-backdrop-filter:blur(28px) saturate(1.8);
  border:1px solid rgba(255,255,255,.75);border-radius:28px;
  box-shadow:0 12px 40px rgba(26,61,124,.1),inset 0 1px 1px rgba(255,255,255,.95),inset 0 -1px 1px rgba(255,255,255,.25)}
[data-tip]{position:relative;cursor:help}
[data-tip]:hover::after{content:attr(data-tip);position:absolute;top:calc(100% + 8px);left:50%;transform:translateX(-50%);
  width:250px;background:rgba(16,22,38,.96);color:#fff;font:500 10.5px/1.55 'Inter';letter-spacing:0;
  text-transform:none;padding:9px 12px;border-radius:11px;z-index:60;text-align:left;white-space:normal;
  box-shadow:0 12px 32px rgba(0,0,0,.32)}
[data-tip]:hover::before{content:"";position:absolute;top:calc(100% + 3px);left:50%;transform:translateX(-50%);
  border:5px solid transparent;border-bottom-color:rgba(16,22,38,.96);z-index:60}
.page{max-width:1232px;margin:0 auto;padding:22px 26px 56px}
@keyframes breathe{0%,100%{transform:scale(1);opacity:.8}50%{transform:scale(1.18);opacity:1}}
/* ============ the sidebar — flat, full-height, Gemini-style ============ */
.siderail{position:fixed;left:0;top:0;bottom:0;z-index:50;width:68px;
  display:flex;flex-direction:column;align-items:center;gap:14px;padding:16px 0;
  background:rgba(248,250,253,.7);backdrop-filter:blur(20px) saturate(1.4);
  -webkit-backdrop-filter:blur(20px) saturate(1.4);
  border-right:1px solid var(--hair)}
.sr-logo{width:40px;height:40px;border-radius:11px;overflow:hidden;flex:none;
  box-shadow:0 4px 12px rgba(62,92,132,.3);margin-bottom:6px}
.sr-logo svg{display:block;width:100%;height:100%}
.sr-sep{width:30px;height:1px;background:var(--hair)}
.sr-btn{display:grid;place-items:center;width:44px;height:44px;border-radius:14px;border:none;
  background:transparent;color:var(--muted);cursor:pointer;transition:.18s}
.sr-btn svg{width:21px;height:21px}
.sr-btn:hover{color:var(--ink);background:rgba(16,22,38,.05)}
.sr-btn.on{color:var(--ink);background:rgba(16,22,38,.08);box-shadow:none}
.panel{padding:0}
.mainhead{display:flex;align-items:center;gap:14px;padding-bottom:20px;margin-bottom:6px;
  border-bottom:1px solid var(--hair)}
.logo-mini{width:48px;height:48px;border-radius:11px;overflow:hidden;flex:none;
  box-shadow:0 5px 14px rgba(62,92,132,.38)}
.logo-mini svg{display:block;width:100%;height:100%}
.mh-title{font-family:var(--disp);font-weight:700;font-size:16px;letter-spacing:-.01em;white-space:nowrap}
.nav{display:flex;gap:4px;padding:4px;border-radius:999px;
  background:rgba(16,22,38,.05);border:1px solid rgba(255,255,255,.5)}
.nav a{font:600 11.5px 'Inter';color:var(--muted);background:transparent;border:none;text-decoration:none;
  border-radius:999px;padding:7px 16px;cursor:pointer;transition:.2s;white-space:nowrap;display:inline-block}
.nav a:hover{color:var(--ink)}
.nav a.on{color:var(--ink);background:rgba(255,255,255,.92);
  box-shadow:0 3px 10px rgba(26,61,124,.12),inset 0 1px 0 #fff}
.mh-live{margin-left:auto;display:inline-flex;align-items:center;gap:7px;
  font:700 8.5px var(--mono);letter-spacing:.16em;color:var(--muted);white-space:nowrap}
.mh-live i{width:7px;height:7px;border-radius:50%;background:#34A853;box-shadow:0 0 8px rgba(52,168,83,.6);animation:breathe 3s ease-in-out infinite}
.deskrow{display:flex;align-items:flex-start;gap:18px;padding:16px 4px 22px}
.deskrow .spacer{flex:1}
.pinbtn{position:relative;border:none;background:transparent;cursor:pointer;padding:0;
  transition:transform .2s;flex:none}
.pinbtn::before{content:"";position:absolute;top:-5px;left:50%;transform:translateX(-50%);
  width:10px;height:10px;border-radius:50%;z-index:2;
  background:radial-gradient(circle at 35% 35%,#ff9d8a,#C0392B);
  box-shadow:0 2px 4px rgba(0,0,0,.35),inset 0 -1px 2px rgba(0,0,0,.25)}
.pinbtn:hover{transform:rotate(0deg) translateY(-2px)}
.pinbtn.on{transform:rotate(0deg)}
.pinbtn.on .sheet{outline:2px solid var(--blue);outline-offset:2px}
.pin-eng{transform:rotate(-1.5deg)}
.pin-eng .sheet{width:72px;height:56px;background:#fff;border:1px solid rgba(16,22,38,.14);
  border-radius:9px;box-shadow:0 6px 14px rgba(26,61,124,.18);
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px}
.pin-eng .fun{display:flex;flex-direction:column;align-items:center;gap:2px}
.pin-eng .fun i{display:block;height:3px;border-radius:2px;background:linear-gradient(90deg,#4285F4,#1E9E57)}
.pin-eng .fun i:nth-child(1){width:26px}.pin-eng .fun i:nth-child(2){width:17px}.pin-eng .fun i:nth-child(3){width:9px}
.pin-eng .n2{font:700 6.5px var(--mono);letter-spacing:.16em;color:var(--ink2)}
.pin-note{transform:rotate(2deg)}
.pin-note .sheet{width:72px;height:56px;background:#FFF6E2;border:1px solid rgba(180,140,60,.3);
  border-radius:7px;box-shadow:0 6px 14px rgba(26,61,124,.16);
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px}
.pin-note .n1{color:var(--clay);font-size:12px;line-height:1}
.pin-note .n2{font:700 6.5px var(--mono);letter-spacing:.12em;color:#8a6a2f}
.pin-cal{transform:rotate(2.5deg)}
.pin-cal .sheet{width:64px;border-radius:8px;overflow:hidden;border:1px solid rgba(16,22,38,.14);
  background:#fff;box-shadow:0 6px 14px rgba(26,61,124,.2);display:block}
.pin-cal .mo{background:var(--clay);color:#fff;font:700 6.5px var(--mono);letter-spacing:.14em;
  padding:4px 0 3px;text-align:center}
.pin-cal .grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;padding:5px 6px 6px}
.pin-cal .grid i{display:block;width:100%;padding-top:100%;border-radius:1.5px;background:rgba(16,22,38,.12)}
.pin-cal .grid i.wk{background:rgba(62,92,132,.3)}
.pin-cal .grid i.hot{background:var(--clay);box-shadow:0 0 4px rgba(217,119,87,.7)}
.view{display:none;position:relative}
.view.on{display:block;animation:vin .3s ease}
@keyframes vin{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.tickwrap{padding:10px 0;border-radius:999px;overflow:hidden;margin-bottom:24px;
  background:linear-gradient(135deg,rgba(255,255,255,.5),rgba(255,255,255,.22));
  border:1px solid rgba(255,255,255,.65);
  -webkit-mask:linear-gradient(90deg,transparent,#000 5%,#000 95%,transparent);
  mask:linear-gradient(90deg,transparent,#000 5%,#000 95%,transparent)}
.ticktrack{display:inline-flex;gap:8px;white-space:nowrap;animation:tick 60s linear infinite}
.tickwrap:hover .ticktrack{animation-play-state:paused}
@keyframes tick{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.tchip{position:relative;display:inline-flex;align-items:center;gap:7px;font:600 11px 'Inter';color:var(--ink2);
  padding:6px 14px;border-radius:999px;background:rgba(255,255,255,.6);
  border:1px solid rgba(255,255,255,.75);box-shadow:0 1px 4px rgba(26,61,124,.05);transition:.3s}
.tchip i{width:5px;height:5px;border-radius:50%;background:var(--dim);transition:.3s}
.tchip.match{border-color:rgba(217,119,87,.6);background:#fff;color:var(--deep)}
.tchip.match i{background:var(--clay);box-shadow:0 0 7px rgba(217,119,87,.7)}
.tchip .plus{position:absolute;top:-8px;right:-3px;font:700 8.5px var(--mono);color:#fff;
  background:var(--clay);border-radius:999px;padding:2px 6px;opacity:0;transform:translateY(3px);transition:.3s}
.tchip.match .plus{opacity:1;transform:none}
/* jobs mode: the tab streams the boards being synthesised, source-style */
#jobsTick{position:relative;padding:12px 0;border-radius:24px}
#jobsTick .sline{display:block;font:500 10.5px/1.7 var(--mono);color:rgba(16,22,38,.32);
  white-space:nowrap;animation-duration:14s}
#jobsTick .sline.s2{animation-duration:19s;animation-direction:reverse}
.synthpill{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  display:inline-flex;align-items:center;background:#fff;border:1px solid rgba(16,22,38,.08);
  border-radius:999px;padding:8px 16px;font:600 13px var(--mono);color:var(--ink);
  box-shadow:0 6px 18px rgba(26,61,124,.16)}
.dotgrid{display:grid;grid-template-columns:repeat(4,4px);gap:2px;margin-right:9px}
.dotgrid i{width:4px;height:4px;border-radius:1px;background:rgba(217,119,87,.25);
  animation:dgfill .85s linear infinite}
.dotgrid i:nth-child(n+5){animation-delay:.14s}
.dotgrid i:nth-child(n+9){animation-delay:.28s}
@keyframes dgfill{0%,18%{background:rgba(217,119,87,.22)}38%,66%{background:var(--clay)}86%,100%{background:rgba(217,119,87,.22)}}
/* the daily stamp: this end-to-end flow runs fresh every day */
.pipedate{font:700 12px var(--mono);letter-spacing:.18em;color:var(--muted);
  padding:14px 2px 18px}
.pipedate .sp{color:var(--clay)}
.pipedate #pipeDate{color:var(--ink2)}
.stages{position:relative;display:grid;grid-template-columns:repeat(5,1fr);gap:16px}
.stages.jobs4{grid-template-columns:repeat(4,1fr)}
.stages.jobs4 .stg.s3{display:none}
/* jobs mode: the scene boxes stand down; the funnel numbers stay */
.stages.noscene .mini{display:none}
.rail{position:absolute;left:10%;right:10%;top:24px;height:2px;z-index:0;
  background:linear-gradient(90deg,rgba(154,160,166,.45),rgba(66,133,244,.5),rgba(26,61,124,.4),rgba(217,122,43,.5),rgba(30,158,87,.55))}
.stages.jobs4 .rail{left:12.5%;right:12.5%}
.fdot{position:absolute;top:-2.5px;width:7px;height:7px;border-radius:50%;background:var(--blue);
  box-shadow:0 0 8px rgba(66,133,244,.65);opacity:0;animation:travel 5.4s linear infinite}
.fdot.d2{animation-delay:1.8s}.fdot.d3{animation-delay:3.6s}
@keyframes travel{0%{left:0;opacity:0}8%{opacity:1}86%{opacity:1}100%{left:calc(100% - 7px);opacity:0}}
.stg{position:relative;z-index:1;text-align:center}
/* ---- mini stage scenes: one uniform card above every pipeline number ---- */
.mini{position:relative;height:104px;margin-bottom:14px;border-radius:14px;overflow:hidden;
  background:rgba(255,255,255,.78);border:1px solid rgba(255,255,255,.9);
  box-shadow:0 4px 14px rgba(26,61,124,.07);text-align:left}
.mini .embers{position:absolute;left:0;right:0;bottom:0;height:34px;pointer-events:none;
  font:700 6.5px/1.1 var(--mono);color:var(--vma);white-space:pre;text-align:center;overflow:hidden}
/* m1 — live crawl table over the ember skyline */
.mini .crawlrows{position:absolute;left:0;right:0;top:4px;bottom:30px;overflow:hidden;
  -webkit-mask:linear-gradient(180deg,#000 55%,transparent);mask:linear-gradient(180deg,#000 55%,transparent)}
.mini .crawltrack{animation:miniScroll 9s linear infinite}
@keyframes miniScroll{from{transform:translateY(0)}to{transform:translateY(-50%)}}
.mini .crow{display:flex;gap:8px;align-items:baseline;padding:4px 10px;font:500 8px var(--mono);color:var(--muted)}
.mini .crow .u{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mini .crow .u b{color:var(--ink);font-weight:700}
.mini .crow .ms2{color:var(--clay);font-weight:700;flex:none}
/* m2 — the trigger families, two pill rows looping leftwards in the box */
.mini .ptrows{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;gap:9px;overflow:hidden;
  -webkit-mask:linear-gradient(90deg,transparent,#000 8%,#000 92%,transparent);
  mask:linear-gradient(90deg,transparent,#000 8%,#000 92%,transparent)}
.mini .ptr{display:inline-flex;gap:6px;white-space:nowrap;animation:tick 42s linear infinite;width:max-content}
.mini .ptr.r2{animation-duration:54s}
.tpill{position:relative;display:inline-flex;align-items:center;gap:5px;font:600 8.5px 'Inter';color:var(--ink2);
  padding:4px 10px;border-radius:999px;background:rgba(255,255,255,.75);
  border:1px solid rgba(16,22,38,.08);transition:.3s}
.tpill i{width:4px;height:4px;border-radius:50%;background:var(--dim);transition:.3s}
.tpill.match{border-color:rgba(217,119,87,.6);background:#fff;color:var(--deep)}
.tpill.match i{background:var(--clay);box-shadow:0 0 6px rgba(217,119,87,.7)}
/* m4 — browser frame, skeleton lines, sweeping scan band, testing pill */
.mini .bframe{position:absolute;inset:10px 12px;border:1.5px solid var(--clay);border-radius:9px;
  background:#fff;overflow:hidden}
.mini .skel{position:absolute;height:6px;border-radius:99px;background:rgba(16,22,38,.08)}
.mini .scanband{position:absolute;left:0;right:0;height:16px;
  background:linear-gradient(180deg,transparent,rgba(217,119,87,.22),transparent);
  animation:scanY 2.4s ease-in-out infinite alternate}
@keyframes scanY{from{top:8%}to{top:72%}}
.mini .ppill{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  display:inline-flex;align-items:center;gap:6px;background:#fff;border:1px solid rgba(16,22,38,.1);
  border-radius:999px;padding:4px 10px;font:600 8px var(--mono);color:var(--ink2);
  box-shadow:0 3px 10px rgba(26,61,124,.12);white-space:nowrap}
.mini .ppill i{width:6px;height:6px;border-radius:2px;background:var(--clay);animation:breathe 1.4s ease-in-out infinite}
/* m3 — structured output, values scrambling live, centred in the box */
.mini .codebox{position:absolute;left:50%;top:46%;transform:translate(-50%,-50%);
  width:max-content;max-width:92%;font:500 7.5px/1.55 var(--mono);color:var(--dim);
  text-align:left;overflow:hidden}
.mini .codebox .k{color:var(--ink2);font-weight:700}
.mini .codebox .v{color:var(--clay);font-weight:700}
.mini .scrchip{position:absolute;right:8px;bottom:7px;background:#fff;border:1px solid rgba(16,22,38,.1);
  border-radius:999px;padding:3px 8px;font:700 7.5px var(--mono);color:var(--ink2);
  box-shadow:0 2px 8px rgba(26,61,124,.1)}
.mini .scrchip b{color:var(--clay)}
/* m4 — document parsed over the ember field */
.mini .doc{position:absolute;left:50%;top:9px;transform:translateX(-50%);width:46px;height:58px;
  background:#fff;border:1px solid rgba(16,22,38,.1);border-radius:5px;
  box-shadow:0 4px 12px rgba(26,61,124,.12);padding:7px 6px;z-index:2}
.mini .doc i{display:block;height:3px;border-radius:99px;background:rgba(16,22,38,.1);margin-bottom:3px}
.mini .doc i.hl{background:var(--clay);opacity:.85}
.mini .doc .ftag{position:absolute;right:-9px;bottom:9px;background:#6b7686;color:#fff;
  font:700 5.5px var(--mono);letter-spacing:.08em;border-radius:3px;padding:2px 4px}
.mini .ppill.low{top:auto;bottom:7px;transform:translateX(-50%)}
/* m5 — paperwork collating itself into the dossier */
.mini .collate{position:absolute;inset:0;z-index:2}
.mini .sheetfly{position:absolute;top:24px;left:12%;width:22px;height:28px;background:#fff;
  border:1px solid rgba(16,22,38,.12);border-radius:3px;padding:4px 3px;opacity:0;
  box-shadow:0 3px 8px rgba(26,61,124,.14);animation:flyin 3.2s ease-in-out infinite}
.mini .sheetfly i{display:block;height:2px;border-radius:99px;background:rgba(16,22,38,.12);margin-bottom:2.5px}
.mini .sheetfly i.hl{background:var(--clay);opacity:.8}
.mini .sheetfly.f2{top:38px;animation-delay:1.05s}
.mini .sheetfly.f3{top:16px;animation-delay:2.1s}
@keyframes flyin{0%{left:6%;opacity:0;transform:rotate(-6deg) scale(1)}
  18%{opacity:1}62%{opacity:1;transform:rotate(3deg) scale(.92)}
  82%{left:42%;opacity:0;transform:rotate(6deg) scale(.55)}100%{left:42%;opacity:0}}
.mini .folderbox{position:absolute;left:50%;transform:translateX(-50%);top:50%;
  margin-top:-24px;width:46px;height:40px;z-index:3}
.mini .folderbox svg{width:100%;height:100%;color:var(--deep);
  filter:drop-shadow(0 4px 8px rgba(26,61,124,.2))}
.mini .folderbox::after{content:"";position:absolute;inset:-5px;border-radius:12px;
  border:1.5px solid rgba(217,119,87,.45);animation:breathe 3.2s ease-in-out infinite}
/* the dissolve field — the radar landing's ASCII ground, light-page tuning */
.mini .dissolve{position:absolute;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}
.mini .folderbox .ftag2{position:absolute;left:50%;transform:translateX(-50%);bottom:-13px;
  font:700 5.5px var(--mono);letter-spacing:.1em;color:var(--deep);white-space:nowrap}
.stg .num{font-family:var(--disp);font-weight:700;font-size:36px;letter-spacing:-.02em;line-height:1;
  color:transparent;background:linear-gradient(125deg,var(--s1),var(--s2));
  -webkit-background-clip:text;background-clip:text}
.stg .lbl{font:700 9px var(--mono);letter-spacing:.2em;color:var(--ink2);margin-top:8px}
.stg .cap{font-size:10.5px;color:var(--dim);margin:6px auto 0;line-height:1.55;max-width:185px}
.stg.s1{--s1:#9AA0A6;--s2:#4285F4}.stg.s2{--s1:#4285F4;--s2:#1A3D7C}
.stg.s3{--s1:#3E5C84;--s2:#1A3D7C}.stg.s4{--s1:#D97A2B;--s2:#B45309}
.stg.s5{--s1:#1E9E57;--s2:#1A3D7C}
.boardbar{display:flex;align-items:center;gap:12px;padding:22px 0 12px}
.seg{display:flex;gap:3px;padding:3px;border-radius:999px;background:rgba(16,22,38,.05);
  border:1px solid rgba(255,255,255,.5)}
.seg button{font:600 10.5px 'Inter';color:var(--muted);background:transparent;border:none;
  border-radius:999px;padding:5px 13px;cursor:pointer;transition:.2s;white-space:nowrap}
.seg button.on{color:var(--ink);background:rgba(255,255,255,.92);box-shadow:0 2px 7px rgba(26,61,124,.1)}
.ctrls{margin-left:auto;display:flex;gap:8px;align-items:center}
.ctrl{position:relative}
.ctrlbtn{display:inline-flex;align-items:center;gap:7px;font:600 10.5px 'Inter';color:var(--ink2);
  padding:6px 13px;border-radius:999px;background:rgba(255,255,255,.7);
  border:1px solid rgba(255,255,255,.85);box-shadow:0 1px 4px rgba(26,61,124,.06);cursor:pointer;transition:.15s}
.ctrlbtn:hover{background:#fff}
.ctrlbtn svg{width:13px;height:13px}
.ctrlmenu{display:none;position:absolute;top:calc(100% + 6px);right:0;min-width:170px;z-index:30;
  background:rgba(255,255,255,.97);border:1px solid var(--hair);border-radius:16px;padding:6px;
  box-shadow:0 14px 38px rgba(26,61,124,.16)}
.ctrlmenu.open{display:block}
.ctrlmenu button{display:flex;width:100%;align-items:center;gap:8px;border:none;background:transparent;
  font:600 11.5px 'Inter';color:var(--ink2);padding:8px 11px;border-radius:10px;cursor:pointer;text-align:left}
.ctrlmenu button:hover{background:rgba(232,240,254,.7)}
.ctrlmenu button.on{background:var(--wash);color:var(--deep)}
.ctrlmenu button.on::after{content:"✓";margin-left:auto;font-weight:800;color:var(--deep)}
.strip{border-bottom:1px solid var(--hair)}
.strip:last-of-type{border-bottom:none}
.strip-h{display:grid;grid-template-columns:34px 1fr 150px 104px 86px 30px;gap:14px;align-items:center;
  padding:15px 4px;cursor:pointer;transition:background .15s;border-radius:16px}
.strip-h:hover{background:rgba(255,255,255,.5)}
.strip-h:focus-visible{outline:2px solid var(--blue);outline-offset:-2px}
.rank{font:700 12px var(--mono);color:var(--dim);text-align:center}
.strip.open .rank{color:var(--clay)}
.idcell{min-width:0}
.idcell .co{font-weight:700;font-size:14.5px;color:#0c1326;display:flex;align-items:center;gap:8px}
.idcell .co .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.idcell .seat{font-size:11px;color:var(--muted);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag-new{font:700 7.5px 'Inter';letter-spacing:.06em;color:#1d4ed8;background:#e9effb;padding:2px 6px;border-radius:999px;flex:none}
.tag-dnc{font:700 7.5px var(--mono);letter-spacing:.1em;color:var(--red);border:1px solid rgba(192,57,43,.4);
  background:#fdecea;padding:2px 6px;border-radius:999px;flex:none}
.tp{justify-self:start;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  padding:3px 11px;border-radius:999px;font:600 10.5px/1.6 'Inter';border:1px solid rgba(255,255,255,.7)}
.tp::before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:5px;vertical-align:middle}
.tp.lead{background:rgba(233,239,251,.85);color:#1d4ed8}.tp.lead::before{background:#3b82f6}
.tp.fund{background:rgba(231,243,236,.85);color:#1e7a41}.tp.fund::before{background:#34A853}
.tp.restr{background:rgba(237,240,244,.85);color:#46556e}.tp.restr::before{background:#6b7689}
.tp.warn{background:rgba(253,236,219,.85);color:#b5530e}.tp.warn::before{background:#D97A2B}
.tp.mna{background:rgba(239,233,251,.85);color:#6b3fb5}.tp.mna::before{background:#8B5CF6}
.tp.people{background:rgba(221,243,240,.85);color:#0e7c74}.tp.people::before{background:#12A594}
.wincell{font:600 9.5px var(--mono);letter-spacing:.05em;color:var(--muted);white-space:nowrap}
.strengthcell{display:flex;align-items:center;gap:8px;justify-self:end}
.strengthcell .sn{font:700 13.5px var(--mono)}
.s-hi{color:#1e7a41}.s-md{color:#9a3412}.s-lo{color:#6b7686}
.chev{justify-self:center;color:var(--dim);transition:transform .25s}
.strip.open .chev{transform:rotate(180deg);color:var(--clay)}
.chev svg{width:15px;height:15px;display:block}
.strip.done-fu .idcell .co .nm{color:#1e7a41}
.strip.done-dis .idcell{opacity:.45;text-decoration:line-through}
.dossier{max-height:0;overflow:hidden;transition:max-height .45s cubic-bezier(.2,.8,.25,1)}
.strip.open .dossier{max-height:1400px}
.jrow{display:grid;grid-template-columns:34px 1fr 150px 120px 64px 64px 40px;gap:14px;align-items:center;
  padding:14px 4px;border-bottom:1px solid var(--hair);cursor:pointer;border-radius:16px;transition:background .15s}
.jrow:last-of-type{border-bottom:none}
.jrow:hover{background:rgba(255,255,255,.5)}
.jrow .jt{font-weight:650;font-size:13px;color:#0c1326}
.jrow .jc{font-size:11px;color:var(--muted);margin-top:2px}
.jrow .geo{font:600 10px var(--mono);color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.jrow .age2{font:600 9px var(--mono);color:var(--dim)}
.jrow .lnk{justify-self:center;color:var(--dim)}
.jrow:hover .lnk{color:var(--deep)}
.jrow .lnk svg{width:14px;height:14px}
.jrow.done-dis .jt,.jrow.done-dis .jc{opacity:.45;text-decoration:line-through}
.jrow.done-fu .jt{color:#1e7a41}
.jacts{display:inline-flex;gap:6px;justify-self:end}
.jact{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:8px;
  color:var(--ink2);cursor:pointer;border:1px solid rgba(60,64,67,.16);background:#fff;transition:.15s}
.jact svg{width:12px;height:12px}
.jact.fu:hover,.jact.fu.on{background:#e7f3ec;color:#1e7a41;border-color:#bfe3cd}
.jact.dis:hover,.jact.dis.on{background:#fdecea;color:var(--red);border-color:#f0c5bd}
.srcpills{justify-self:start;display:flex;flex-wrap:wrap;gap:4px;min-width:0}
.srcpill{font:600 9.5px 'Inter';color:var(--ink2);padding:3px 10px;border-radius:999px;
  background:rgba(255,255,255,.75);border:1px solid rgba(16,22,38,.1);max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.port{margin:6px 2px 20px;background:rgba(255,255,255,.94);border:1px solid rgba(255,255,255,.95);
  border-radius:22px;box-shadow:0 18px 50px rgba(26,61,124,.12),inset 0 1px 0 #fff}
.port-top{display:flex;align-items:flex-start;gap:20px;padding:22px 28px 18px;border-bottom:2px solid var(--ink)}
.port-top .eyeb{font:700 8px var(--mono);letter-spacing:.24em;color:var(--clay)}
.port-top .pco{font-family:var(--disp);font-weight:700;font-size:24px;letter-spacing:-.015em;margin-top:6px}
.port-top .pmand{font-size:12.5px;color:var(--muted);margin-top:4px}
.port-top .pmand b{color:var(--ink2);font-weight:600}
.port-top .pright{margin-left:auto;display:flex;align-items:center;gap:16px;flex:none}
.port-top .pchips{display:flex;flex-direction:column;gap:5px;align-items:flex-end}
.scorering{position:relative;width:62px;height:62px;flex:none}
.scorering .sv{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font:700 15px var(--mono)}
.prow{display:grid;grid-template-columns:140px 1fr;gap:18px;padding:13px 28px;border-bottom:1px solid rgba(16,22,38,.06);align-items:baseline}
.prow .pl2{font:700 8.5px var(--mono);letter-spacing:.18em;color:var(--dim)}
.prow .pv{font-size:12.5px;line-height:1.62;color:var(--ink2);min-width:0}
.prow .pv b{color:var(--ink);font-weight:650}
.prow.alt{background:rgba(62,92,132,.025)}
.chip{font:700 8.5px var(--mono);letter-spacing:.08em;padding:4px 11px;border-radius:999px;border:1px solid var(--hair);white-space:nowrap}
.chip.fee{color:#b5530e;background:#fdecdb}
.chip.q4{color:#9a3412;background:#fff7ed}
.chip.ver-reg{color:#1e7a41;background:#e7f3ec}
.chip.ver-multi{color:#1d4ed8;background:#e9effb}
.chip.ver-single{color:#9a3412;background:#fff7ed}
.chip.rt{color:#0e7c74;background:#ddf3f0}
.chip.prop{color:#46556e;background:#edf0f4}
.chip.anti{color:var(--red);background:#fdecea}
.srcl{display:flex;align-items:baseline;gap:9px;padding:4px 0;font-size:12px;color:inherit;text-decoration:none;border-radius:8px}
.srcl:hover .el2{color:var(--deep);text-decoration:underline}
.srcl .ek{font:700 8px var(--mono);letter-spacing:.1em;color:var(--dim);width:56px;flex:none}
.srcl .el2{flex:1;min-width:0;color:var(--ink2)}
.srcl .ed2{font:500 9.5px var(--mono);color:var(--dim);flex:none}
.srcl .ext{width:11px;height:11px;color:var(--dim);flex:none;align-self:center}
.qmarks{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.qmark{font:600 11px 'Inter';color:var(--ink2)}
.qmark b{font:700 12px var(--mono)}
.gatebox{padding:11px 14px;border-radius:11px;font-size:12.5px;line-height:1.6;
  background:#fff7ed;border:1px solid #fdba74;color:#9a3412}
.port-foot{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:16px 28px 18px;background:rgba(62,92,132,.04);
  border-radius:0 0 22px 22px}
.btn{display:inline-flex;align-items:center;gap:7px;font:600 11px 'Inter';padding:8px 16px;border-radius:999px;cursor:pointer;transition:.15s;
  color:var(--ink);background:#fff;border:1px solid var(--border);box-shadow:0 1px 5px rgba(26,61,124,.07)}
.btn svg{width:13px;height:13px;flex:none}
.btn:hover{transform:translateY(-1px);box-shadow:0 4px 12px rgba(26,61,124,.12)}
.btn.primary{color:var(--deep);background:transparent;border:1px solid rgba(26,61,124,.4);font-weight:700;box-shadow:none}
.btn.primary:hover{background:rgba(26,61,124,.06)}
.btn.busy{color:var(--dim);pointer-events:none}
.btn.good.on{color:#1e7a41;background:#f3faf5;border-color:#bfe3cd}
.btn.bad.on{color:#c0392b;background:#fdf4f2;border-color:#f0c5bd}
.gap{flex:1}
.openerbox{margin:14px 28px 0;padding:11px 14px;border-left:2px solid var(--vma);border-radius:0 12px 12px 0;
  background:rgba(62,92,132,.06);font-size:12.5px;line-height:1.62;color:var(--ink2)}
.bl-toggle{display:inline-flex;align-items:center;gap:8px;font:600 11.5px 'Inter';color:var(--muted);
  background:none;border:none;cursor:pointer;padding:14px 4px 2px}
.bl-toggle:hover{color:var(--deep)}
.bl-toggle .chev2{transition:transform .25s;display:inline-flex}
.bl-toggle.on .chev2{transform:rotate(180deg)}
.bl-toggle svg{width:13px;height:13px}
.bl-list{max-height:0;overflow:hidden;transition:max-height .35s ease}
.bl-list.on{max-height:560px;overflow-y:auto}
.bl-row{display:grid;grid-template-columns:34px 1fr 150px auto;gap:14px;align-items:center;
  padding:10px 4px;border-bottom:1px solid rgba(16,22,38,.05);opacity:.78}
.bl-row .co2{font-weight:600;font-size:12.5px;display:flex;gap:8px;align-items:center;min-width:0}
.bl-row .gw{font-size:10.5px;color:var(--amb);text-align:right;max-width:420px}
.bl-row.blocked .gw{color:var(--red)}
/* calendar */
.calwrap{position:relative}
.calgrid{border:1px solid rgba(255,255,255,.75);border-radius:20px;overflow:hidden;background:rgba(255,255,255,.4)}
.cg-head{display:grid;grid-template-columns:120px repeat(7,1fr)}
.cg-head span{font:700 8.5px var(--mono);letter-spacing:.16em;color:var(--muted);padding:12px 0 10px;text-align:center;
  border-left:1px solid rgba(16,22,38,.05);border-bottom:1px solid var(--hair);position:relative}
.cg-head span:first-child{border-left:none}
.cg-head span.today{color:var(--clay)}
.cg-head span.today::after{content:attr(data-td);position:absolute;left:50%;transform:translateX(-50%);
  bottom:-9px;font:700 7px var(--mono);letter-spacing:.1em;color:#fff;background:var(--clay);
  border-radius:999px;padding:2px 8px;white-space:nowrap;z-index:3}
.cg-row{display:grid;grid-template-columns:120px repeat(7,1fr);border-bottom:1px solid rgba(16,22,38,.05)}
.cg-row:last-child{border-bottom:none}
.cg-lab{display:flex;flex-direction:column;justify-content:center;gap:3px;padding:14px;border-right:1px solid rgba(16,22,38,.05)}
.cg-lab .t{font:700 8.5px var(--mono);letter-spacing:.14em}
.cg-lab .c{font:500 9px 'Inter';color:var(--dim)}
.cg-cell{padding:10px 6px;border-left:1px solid rgba(16,22,38,.04);display:flex;flex-direction:column;gap:6px;min-height:60px}
.cg-cell:nth-child(2){border-left:none}
.cpill{display:flex;align-items:center;gap:7px;font:600 10px 'Inter';color:var(--ink2);
  padding:5px 10px;border-radius:999px;background:rgba(255,255,255,.5);
  border:1px solid rgba(16,22,38,.08);box-shadow:0 1px 4px rgba(26,61,124,.06);
  cursor:pointer;transition:.15s;min-width:0}
.cpill.sel{background:#fff;border-color:rgba(66,133,244,.4)}
.cpill:hover{transform:translateY(-1px);box-shadow:0 6px 14px rgba(26,61,124,.13)}
.cpill i{width:6px;height:6px;border-radius:50%;flex:none}
.cpill.ev i{background:#3b82f6}
.cpill.wi i{background:#12A594}
.cpill.fw i{background:#8B5CF6}
.cpill .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cpill .d{margin-left:auto;font:700 8px var(--mono);color:var(--dim);flex:none}
.cpill.openg i{box-shadow:0 0 7px rgba(18,165,148,.8);animation:breathe 3s ease-in-out infinite}
/* window bars: they start and end exactly where the window does */
.wtrack{position:relative;grid-column:2/-1;border-left:1px solid rgba(16,22,38,.04)}
.wtrack .mline{position:absolute;top:0;bottom:0;width:1px;background:rgba(16,22,38,.04);pointer-events:none}
.wspan{position:absolute;height:24px;display:flex;align-items:center;gap:7px;
  padding:0 10px;border-radius:999px;font:600 10px 'Inter';color:var(--ink2);
  background:rgba(255,255,255,.5);border:1px solid rgba(16,22,38,.08);
  box-shadow:0 1px 4px rgba(26,61,124,.06);cursor:pointer;transition:background .15s,box-shadow .15s;min-width:0;overflow:hidden}
.wspan:hover{box-shadow:0 6px 14px rgba(26,61,124,.13)}
.wspan.sel{background:#fff;border-color:rgba(66,133,244,.4)}
.wspan i{width:6px;height:6px;border-radius:50%;background:#12A594;flex:none}
.wspan.openg i{box-shadow:0 0 7px rgba(18,165,148,.8);animation:breathe 3s ease-in-out infinite}
.wspan .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wspan .d{margin-left:auto;font:700 8px var(--mono);color:var(--dim);flex:none}
.legend{display:flex;gap:18px;margin-top:14px;font:600 9.5px 'Inter';color:var(--muted)}
.legend i{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:0}
.cal-pop{position:absolute;z-index:30;width:300px;padding:15px 17px;display:none;border-radius:20px;
  background:#fff;border:1px solid var(--hair);box-shadow:0 18px 50px rgba(26,61,124,.2)}
.cal-pop.on{display:block}
.cal-pop .pk{font:700 8.5px var(--mono);letter-spacing:.16em;margin-bottom:6px}
.cal-pop .pn{font-weight:700;font-size:13.5px;color:#0c1326}
.cal-pop .pd{font:600 10px var(--mono);color:var(--dim);margin-top:4px}
.cal-pop .pwl{font:700 8px var(--mono);letter-spacing:.18em;color:var(--clay);margin-top:12px}
.cal-pop .pw{font-size:12px;line-height:1.55;color:var(--ink2);margin-top:5px}
.cal-pop .psrc{display:inline-flex;align-items:center;gap:6px;margin-top:12px;font:600 11px 'Inter';
  color:var(--deep);text-decoration:none}
.cal-pop .psrc:hover{text-decoration:underline}
.cal-pop .psrc svg{width:11px;height:11px}
/* workshop (the live Personal Assistant section, renamed) */
/* centred within the page column, exactly like the old PA page */
.shopwrap{max-width:900px;margin:0 auto;padding:10px 0 8px}
.ea-hero{text-align:center;margin-bottom:26px}
.cc-bigicon{width:78px;height:78px;border-radius:18px;margin:0 auto 22px;display:grid;
  place-items:center;color:#1F1F1F;background:transparent}
.cc-bigicon svg{width:52px;height:52px}
.gemini-title{font-family:"Newsreader",Georgia,serif;font-weight:400;font-size:34px;
  letter-spacing:-.01em;color:var(--ink);text-align:center}
.cc-sub{font-size:15px;color:var(--muted);margin-top:11px}
.composer{box-sizing:content-box;width:672px;max-width:100%;background:#fff;
  border:1px solid transparent;border-radius:20px;
  box-shadow:0 4px 20px rgba(0,0,0,.07),0 0 0 .5px rgba(31,31,30,.286);
  padding:0;margin:0 auto;text-align:left;transition:border-color .2s,box-shadow .2s}
.composer:focus-within{border-color:rgba(31,31,30,.20)}
.composer .inner{display:flex;flex-direction:column;margin:14px;gap:12px}
.composer .cform{min-height:48px;max-height:384px;overflow-y:auto;padding:6px 0 0 6px;transition:all .2s}
.composer .cfoot{display:flex;justify-content:flex-end;align-items:center}
.composer:not([data-mode="free"]) .cfoot .send{display:none}
.cinput{border:none;outline:none;background:transparent;width:100%;
  font-family:"Inter",system-ui,sans-serif;font-size:16px;line-height:22.4px;font-weight:430;color:rgb(11,11,11)}
.cinput::placeholder{color:var(--dim)}
.cfh{display:flex;align-items:center;gap:9px;font-size:15px;font-weight:600;color:var(--ink);margin-bottom:4px}
.cfh .cf-dot{width:7px;height:7px;border-radius:50%;background:var(--blue)}
.cf-desc{font-size:12.5px;color:var(--muted);margin-top:7px;line-height:1.5}
.composer .cform form{display:flex;flex-direction:column}
.composer .cform label{display:block;font:600 9.5px/1 "JetBrains Mono",monospace;
  letter-spacing:.12em;text-transform:uppercase;color:var(--ink2);margin:15px 0 7px}
.composer .cform input:not(.cinput){width:100%;padding:11px 14px;border:1px solid var(--border);border-radius:10px;
  font:400 13.5px/1.3 "Inter",sans-serif;color:var(--ink);background:#fff}
.composer .cform input::placeholder{color:var(--dim)}
.composer .cform input:not(.cinput):focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 3px rgba(66,133,244,.12)}
.send{width:30px;height:30px;border-radius:50%;background:#a8c8e6;color:#1A3D7C;
  display:grid;place-items:center;font-size:16px;border:none;flex-shrink:0;transition:all .14s;cursor:pointer}
.send:hover{background:#93b9de;color:#10294f}
.send svg{width:16px;height:16px}
.composer .cform .send{align-self:flex-end;order:99;margin-top:10px}
.cap-form{display:none}
.cap-form.active{display:block}
.composer .cform .status{margin-top:10px;padding:8px 11px;border-radius:8px;
  font-size:11.5px;display:none;line-height:1.4}
.composer .cform .status.ok{background:var(--wash);color:var(--deep);
  border-left:2px solid var(--blue);display:block}
.composer .cform .status.err{background:rgba(201,59,43,.08);color:#8B2C20;
  border-left:2px solid #C93B2B;display:block}
.shopchips{display:flex;flex-wrap:wrap;gap:9px;justify-content:center;margin:22px auto 0;max-width:600px}
.shopchip{display:inline-flex;align-items:center;gap:8px;background:transparent;
  border:1px solid var(--border);border-radius:12px;padding:10px 16px;
  font:600 13px 'Inter';color:var(--ink);cursor:pointer;transition:.14s}
.shopchip:hover{background:rgba(244,247,252,.9);border-color:rgba(60,64,67,.3)}
.shopchip .i{color:var(--ink2);font-size:13px}
.shopchip.active{background:var(--ink);color:#fff;border-color:var(--ink)}
.shopchip.active .i{color:#fff}
.toast{position:fixed;bottom:24px;left:50%;transform:translate(-50%,16px);z-index:70;
  background:rgba(16,22,38,.92);backdrop-filter:blur(10px);color:#fff;font:600 12px 'Inter';
  padding:11px 18px;border-radius:999px;box-shadow:0 8px 26px rgba(0,0,0,.25);opacity:0;transition:.25s;pointer-events:none}
.toast.show{opacity:1;transform:translate(-50%,0)}
.toast .sp{color:#FF8A65}
.foot{text-align:center;padding:18px 0 10px}
.dev-zone{display:inline-block;margin-top:10px;padding-top:8px;border-top:1px dashed var(--hair);
  opacity:.45;font-size:10px;color:var(--muted)}
.dev-zone:hover{opacity:.8}
.dev-zone-label{color:var(--dim);font-style:italic;margin-right:6px}
.dev-btn{font:inherit;font-size:10px;color:var(--muted);background:transparent;
  border:1px solid rgba(60,64,67,.25);border-radius:4px;padding:2px 8px;cursor:pointer}
.dev-btn:hover{color:var(--ink);border-color:var(--dim)}
.dev-btn:disabled{opacity:.5;cursor:default}
.dev-status{margin-left:8px;color:var(--dim)}
@media(max-width:1020px){
  .strip-h{grid-template-columns:30px 1fr 96px 30px}
  .strip-h .tp,.strip-h .wincell{display:none}
  .prow{grid-template-columns:110px 1fr}
  .jrow{grid-template-columns:30px 1fr 64px 40px}
  .jrow .srcpills,.jrow .geo,.jrow .age2{display:none}
  .mainhead,.deskrow{flex-wrap:wrap}
}
</style>
</head>
<body>
<div class="amb a1"></div><div class="amb a2"></div><div class="amb a3"></div>

<nav class="siderail">
  <span class="sr-logo"><svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg"><rect width="100" height="100" fill="#3E5C84"/><text x="50" y="55" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-weight="800" font-size="30" letter-spacing="-1.5" fill="#fff">VMA</text><text x="51" y="76" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" font-weight="300" font-size="13.5" letter-spacing="3" fill="#fff">GROUP</text></svg></span>
  <span class="sr-sep"></span>
  <button type="button" class="sr-btn on" id="vbEngine" title="Leads">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 19.5h17"/><path d="M4.5 15.5l4.5-4.5 3.5 3.5 7-7.5"/><path d="M15.5 7h4v4"/></svg>
  </button>
  <button type="button" class="sr-btn" id="vbCal" title="Calendar">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="4.5" width="18" height="16.5" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/></svg>
  </button>
  <button type="button" class="sr-btn" id="vbShop" title="Build-A-Deck">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M7.2 5.5c1.2-.85 8.4-.85 9.6 0"/><path d="M4.5 9.2c0-.95.8-1.7 1.75-1.7h11.5c.95 0 1.75.75 1.75 1.7v7.3a3.5 3.5 0 0 1-3.5 3.5H8a3.5 3.5 0 0 1-3.5-3.5z"/></svg>
  </button>
</nav>

<div class="page">
  <section class="panel">

    <div class="mainhead">
      <span class="mh-title">Business Development Engine</span>
      <nav class="nav" style="margin-left:auto">
        <a href="/comms" class="{{ 'on' if desk_key=='comms' else '' }}">Communications</a>
        <a href="/marketing" class="{{ 'on' if desk_key=='marketing' else '' }}">Marketing</a>
      </nav>
    </div>

    <!-- ============ VIEW: engine + board ============ -->
    <div class="view on" id="v-engine">
      <div class="pipedate"><span class="sp">✦</span> AGENT PIPELINE · <span id="pipeDate"></span></div>
      <div class="tickwrap" id="jobsTick" style="display:none">
        <div class="ticktrack sline" id="jt"></div>
        <div class="ticktrack sline s2" id="jt2"></div>
        <span class="synthpill"><span class="dotgrid"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></span>Synthesising..</span>
      </div>
      <div class="stages" id="stagesEl">
        <div class="rail"><span class="fdot"></span><span class="fdot d2"></span><span class="fdot d3"></span></div>
        <div class="stg s1">
          <div class="mini"><div class="crawlrows"><div class="crawltrack" id="crawlTrack"></div></div>
            <div class="embers" data-ember></div></div>
          <div class="num" id="st1">0</div><div class="lbl" id="sl1"></div><div class="cap" id="cap1"></div></div>
        <div class="stg s2">
          <div class="mini"><div class="ptrows">
            <span class="ptr" id="m2r1"></span>
            <span class="ptr r2" id="m2r2"></span></div></div>
          <div class="num" id="st2">0</div><div class="lbl" id="sl2"></div><div class="cap" id="cap2"></div></div>
        <div class="stg s3">
          <div class="mini"><div class="codebox" id="codeBox"></div>
            <span class="scrchip"><b>::</b> <span id="scrTxt"></span></span></div>
          <div class="num" id="st3">0</div><div class="lbl" id="sl3"></div><div class="cap" id="cap3"></div></div>
        <div class="stg s4">
          <div class="mini"><div class="bframe">
            <span class="skel" style="left:9%;top:14%;width:52%"></span>
            <span class="skel" style="left:9%;top:32%;width:70%"></span>
            <span class="skel" style="left:9%;top:50%;width:44%"></span>
            <span class="skel" style="left:9%;top:68%;width:62%"></span>
            <span class="skel" style="left:9%;top:84%;width:36%"></span>
            <div class="scanband"></div>
            <span class="ppill"><i></i>VMA Testing</span></div></div>
          <div class="num" id="st4">0</div><div class="lbl" id="sl4"></div><div class="cap" id="cap4"></div></div>
        <div class="stg s5">
          <div class="mini"><div class="collate">
            <span class="sheetfly"><i style="width:80%"></i><i style="width:55%"></i><i class="hl" style="width:90%"></i><i style="width:65%"></i></span>
            <span class="sheetfly f2"><i style="width:70%"></i><i class="hl" style="width:85%"></i><i style="width:50%"></i><i style="width:75%"></i></span>
            <span class="sheetfly f3"><i class="hl" style="width:88%"></i><i style="width:60%"></i><i style="width:78%"></i><i style="width:45%"></i></span>
            <span class="folderbox"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7.5V18a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9.5a2 2 0 0 0-2-2h-8l-2-2.5H5a2 2 0 0 0-2 2.5z"/><path d="M8 14.5h8M8 17h5" stroke-width="1.3"/></svg><span class="ftag2">BD PORTFOLIO</span></span></div>
            <canvas class="dissolve" id="m5fx"></canvas></div>
          <div class="num" id="st5">0</div><div class="lbl" id="sl5"></div><div class="cap" id="cap5"></div></div>
      </div>
      <div class="boardbar">
        <div class="seg" id="modeSeg">
          <button data-m="leads" class="on">Warm Signals</button>
          <button data-m="jobs">Live Jobs</button>
        </div>
        <div class="ctrls" id="leadCtrls">
          <div class="ctrl"><button type="button" class="ctrlbtn" data-menu="filtmenu">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5h18l-7 8.2V19l-4 2v-7.8z"/></svg>
            <span id="filtLbl"></span></button>
            <div class="ctrlmenu" id="filtmenu"></div></div>
          <div class="ctrl"><button type="button" class="ctrlbtn" data-menu="sortmenu" title="Sort">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M4 6h16M4 12h11M4 18h6"/></svg>
            <span id="sortLbl"></span></button>
            <div class="ctrlmenu" id="sortmenu"></div></div>
        </div>
      </div>
      <div id="strips"></div>
      <button type="button" class="bl-toggle" id="blToggle">
        <span class="chev2"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg></span>
        <span id="blLabel"></span>
      </button>
      <div class="bl-list" id="blList"></div>
    </div>

    <!-- ============ VIEW: calendar ============ -->
    <div class="view" id="v-cal">
      <div class="calwrap">
        <div class="calgrid">
          <div class="cg-head" id="cgHead"></div>
          <div id="cgRows"></div>
        </div>
        <div class="cal-pop" id="calPop"></div>
      </div>
      <div class="legend">
        <span><i style="background:#3b82f6"></i>Events &amp; networking</span>
        <span><i style="background:#12A594"></i>Placement windows</span>
        <span><i style="background:#8B5CF6"></i>Approved frameworks</span>
      </div>
    </div>

    <!-- ============ VIEW: build-a-deck workshop ============ -->
    <div class="view" id="v-shop">
      <div class="shopwrap">
        <div class="ea-hero">
          <div class="cc-bigicon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M7.2 5.5c1.2-.85 8.4-.85 9.6 0"/><path d="M4.5 9.2c0-.95.8-1.7 1.75-1.7h11.5c.95 0 1.75.75 1.75 1.7v7.3a3.5 3.5 0 0 1-3.5 3.5H8a3.5 3.5 0 0 1-3.5-3.5z"/></svg></div>
          <h1 class="gemini-title">Build-A-Deck</h1>
          <div class="cc-sub">A simple prompt to build key reports in real-time, with the latest data.</div>
        </div>

        <div class="composer" data-mode="free" id="composer">
          <div class="inner">
            <div class="cform" data-cform>
              <input class="cinput" id="cprompt" placeholder="Tell me what to make…">

              <div class="cap-form" data-cap="pitch">
                <div class="cfh"><span class="cf-dot"></span>Pitch Pack</div>
                <div class="cf-desc">Generate a tailored proposal to upgrade a client's job vacancy into an exclusive, retained search.</div>
                <form id="pitch-form" onsubmit="dispatchDeck(event,'pitch-form','/api/dispatch/pitch-pack')">
                  <label for="pp-account">Account name</label>
                  <input id="pp-account" name="account_name" placeholder="e.g. Unilever" required>
                  <label for="pp-role">Role</label>
                  <input id="pp-role" name="role" placeholder="e.g. {{ example_role }}" required>
                  <button type="submit" class="send" title="Run"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
                  <div class="status" id="pitch-status"></div>
                </form>
              </div>

              <div class="cap-form" data-cap="reverse">
                <div class="cfh"><span class="cf-dot"></span>Reverse Match</div>
                <div class="cf-desc">Take a candidate, search the market fresh, and give a ranked list of accounts to match them to.</div>
                <form id="rm-form" onsubmit="dispatchDeck(event,'rm-form','/api/dispatch/reverse-match')">
                  <label for="rm-name">Candidate name</label>
                  <input id="rm-name" name="candidate_name" placeholder="e.g. Rebecca Torres" required>
                  <label for="rm-company">Current company</label>
                  <input id="rm-company" name="current_company" placeholder="e.g. Vodafone" required>
                  <label for="rm-title">Current title</label>
                  <input id="rm-title" name="current_title" placeholder="e.g. {{ example_role }}" required>
                  <button type="submit" class="send" title="Run"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
                  <div class="status" id="rm-status"></div>
                </form>
              </div>

              <div class="cap-form" data-cap="premeeting">
                <div class="cfh"><span class="cf-dot"></span>Pre-meeting Brief</div>
                <div class="cf-desc">Walk into any client meeting with up-to-date prep.</div>
                <form id="pm-form" onsubmit="dispatchDeck(event,'pm-form','/api/dispatch/pre-meeting')">
                  <label for="pm-account">Account name</label>
                  <input id="pm-account" name="account_name" placeholder="e.g. Severn Trent" required>
                  <label for="pm-contact">Contact (optional)</label>
                  <input id="pm-contact" name="contact_name" placeholder="e.g. Carla Sherry">
                  <label for="pm-context">Meeting context (optional)</label>
                  <input id="pm-context" name="meeting_context" placeholder="e.g. 10am Mon, Zoom">
                  <button type="submit" class="send" title="Run"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
                  <div class="status" id="pm-status"></div>
                </form>
              </div>

              <div class="cap-form" data-cap="sweep">
                <div class="cfh"><span class="cf-dot"></span>Manual Sweep</div>
                <div class="cf-desc">Sweep for potential missed leads or pre-market signals.</div>
                <form id="sweep-form" onsubmit="dispatchDeck(event,'sweep-form','/api/dispatch/sweep')">
                  <label for="sw-days">Window (days)</label>
                  <input id="sw-days" name="window_days" type="number" min="1" max="60" placeholder="e.g. 14" required>
                  <button type="submit" class="send" title="Run"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
                  <div class="status" id="sweep-status"></div>
                </form>
              </div>

            </div>
            <div class="cfoot"><button class="send" id="composerSend" type="button" title="Run"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button></div>
          </div>
        </div>

        <div class="shopchips" id="shopChips">
          <button class="shopchip" data-cap="pitch"><span class="i">✦</span>Pitch Pack</button>
          <button class="shopchip" data-cap="reverse"><span class="i">↗</span>Reverse Match</button>
          <button class="shopchip" data-cap="premeeting"><span class="i">◷</span>Pre-meeting</button>
          <button class="shopchip" data-cap="sweep"><span class="i">⟲</span>Sweep</button>
        </div>
      </div>
    </div>

  </section>
  <div class="foot" id="devFoot">
    <span class="dev-zone">
      <span class="dev-zone-label">For dev only - not a user feature:</span>
      <button type="button" onclick="refreshBrief()" id="refresh-btn" class="dev-btn"
              title="Load today's latest brief (the data normally auto-loads; this forces a reload now). Last refreshed: {{ last_updated }}">
        <span class="rbtn-label">Daily Refresh</span>
      </button>
      <button type="button" id="dev-run-brief" class="dev-btn" onclick="devTriggerBrief()"
              title="Maintenance: triggers a fresh morning-brief workflow run. Not for day-to-day use — Daily Refresh just loads the last completed run.">
        trigger fresh data
      </button>
      <span class="dev-status" id="dev-run-status"></span>
    </span>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
window.MR_BD={{ mr_bd|tojson }};
window.MR_JOBS={{ mr_jobs|tojson }};
window.MR_META={{ mr_meta|tojson }};
window.ENG={{ eng_counts|tojson }};
window.TRIG={{ eng_triggers|tojson }};
window.JBOARDS={{ eng_jobboards|tojson }};
window.CAL_EVENTS={{ eng_events|tojson }};
window.CAL_PULSES={{ eng_pulses|tojson }};
window.CAL_FW={{ eng_frameworks|tojson }};
window.CAL_MONTHS={{ eng_months|tojson }};
window.DESK={{ desk_key|tojson }};
</script>
<script>
/* ---- dev-only footer controls: same contract as the legacy dashboard ---- */
async function refreshBrief() {
  const btn = document.getElementById('refresh-btn');
  if (!btn) return;
  const lbl = btn.querySelector('.rbtn-label');
  const originalLabel = lbl.textContent;
  btn.disabled = true;
  lbl.textContent = 'Refreshing…';
  try {
    const r = await fetch('/api/refresh', { method: 'POST' });
    const j = await r.json();
    if (j.ok && (j.leads > 0 || j.predictors > 0)) {
      lbl.textContent = 'Scanning hires…';
      try { await fetch('/api/cascade/scour', { method: 'POST' }); } catch (e) {}
      setTimeout(() => window.location.reload(), 400);
    } else {
      lbl.textContent = originalLabel;
      btn.disabled = false;
      const st = document.getElementById('dev-run-status');
      if (st) st.textContent = (j && j.detail) || 'refresh found nothing new';
    }
  } catch (e) {
    lbl.textContent = originalLabel;
    btn.disabled = false;
    const st = document.getElementById('dev-run-status');
    if (st) st.textContent = 'refresh failed: ' + e.message;
  }
}
async function devTriggerBrief() {
  const btn = document.getElementById('dev-run-brief');
  const status = document.getElementById('dev-run-status');
  if (!confirm(
    'DEVELOPER / TECH ACTION — not a day-to-day feature.\n\n' +
    'This starts a fresh morning-brief scour on GitHub Actions ' +
    '(~5–8 min, no email sent). The user-facing control is ' +
    '"Daily Refresh", which just loads the last completed run.\n\n' +
    'Proceed with the maintenance run?'
  )) return;
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = 'dispatching…';
  status.textContent = '';
  try {
    const r = await fetch('/api/dispatch/brief', { method: 'POST' });
    const j = await r.json();
    status.textContent = j.ok
      ? 'dispatched — ~5–8 min, then click Daily Refresh'
      : ('failed: ' + (j.detail || ('HTTP ' + r.status)));
  } catch (e) {
    status.textContent = 'network error: ' + e.message;
  }
  btn.disabled = false;
  btn.textContent = orig;
}
/* ---- the live dispatch (verbatim contract: popup now, poll until ready) -- */
async function dispatchDeck(event, formId, url) {
  event.preventDefault();
  const form = document.getElementById(formId);
  const btn = form.querySelector('button[type=submit]');
  const status = form.querySelector('.status');
  const data = {};
  new FormData(form).forEach((v, k) => { data[k] = v; });
  data.mode = 'send';
  const win = window.open('', '_blank');
  if (win) {
    win.document.write(
      '<!doctype html><meta charset="utf-8"><title>Preparing report…</title>' +
      '<style>body{font-family:Inter,system-ui,sans-serif;background:#f7f9fc;' +
      'color:#1F1F1F;display:flex;min-height:100vh;align-items:center;' +
      'justify-content:center;margin:0}.b{text-align:center;padding:24px}' +
      '.s{width:30px;height:30px;border:3px solid rgba(66,133,244,.20);' +
      'border-top-color:#4285F4;border-radius:50%;margin:0 auto 16px;' +
      'animation:r .8s linear infinite}@keyframes r{to{transform:rotate(360deg)}}' +
      'p{font-size:13px;color:#5F6368;max-width:340px;line-height:1.55}</style>' +
      '<div class="b"><div class="s"></div><h3>Preparing your report…</h3>' +
      '<p>This can take a few minutes. Keep this tab open — it loads ' +
      'automatically when ready.</p></div>');
  }
  btn.disabled = true;
  status.className = 'status ok'; status.style.display = '';
  status.textContent = 'Dispatching…';
  let j, attempt = 0, lastErr = null;
  while (attempt < 3) {
    attempt++;
    if (attempt > 1) {
      status.textContent = 'Dispatching… retrying (' + attempt + '/3)';
      await new Promise(res => setTimeout(res, 2000));
    }
    try {
      const r = await fetch(url, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data) });
      j = await r.json(); lastErr = null; break;
    } catch (e) { lastErr = e; }
  }
  if (lastErr || !j || !j.ok || !j.artifact || !j.dispatched_at) {
    if (win && !win.closed) win.close();
    status.textContent = (j && j.detail) || (lastErr && ('Network error: ' + lastErr.message)) || 'Failed.';
    status.className = 'status err'; btn.disabled = false;
    return;
  }
  status.textContent = 'Running… the report opens in the new tab when ready.';
  const qs = 'artifact=' + encodeURIComponent(j.artifact) +
             '&since=' + encodeURIComponent(j.dispatched_at);
  const started = Date.now(), MAX_MS = 25 * 60 * 1000;
  const poll = async () => {
    if (Date.now() - started > MAX_MS) {
      if (win && !win.closed) win.close();
      status.textContent = 'Still running after 25 min — the emailed copy will still arrive.';
      status.className = 'status err'; btn.disabled = false; return;
    }
    let s;
    try { const rr = await fetch('/api/output/status?' + qs); s = await rr.json(); }
    catch (e) { s = { ready: false }; }
    if (s.ready && s.id) {
      const viewUrl = '/api/output/view?artifact=' + encodeURIComponent(j.artifact) +
        '&id=' + encodeURIComponent(s.id);
      if (win && !win.closed) win.location = viewUrl;
      status.innerHTML = 'Report ready · <a href="' + viewUrl + '" target="_blank">open ↗</a>';
      btn.disabled = false; return;
    }
    setTimeout(poll, 12000);
  };
  setTimeout(poll, 12000);
}
</script>
<script>
(function(){
"use strict";
const $=id=>document.getElementById(id);
const esc=s=>(s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const BD=(window.MR_BD||[]).map((l,i)=>({...l,_id:'b'+i,status:l.status||'active'}));
const JOBS=(window.MR_JOBS||[]).map((l,i)=>({...l,_id:'j'+i,status:l.status||'active'}));
const BYID={};BD.concat(JOBS).forEach(l=>{BYID[l._id]=l;});
const TIER_WORD={dev:'Developing',early:'Watch',blocked:'Blocked',ready:'Ready'};
const VER_LABEL={reg:'Registry-attested',multi:'2+ sources',single:'Single source'};
const MEANING={
  seat:"SEAT — is there a real, vacant or forming senior seat to fill?",
  budget:"BUDGET — is there money behind the hire?",
  urgency:"URGENCY — is there a forcing date that makes this move now?",
  buyer:"BUYER — is the decision maker identified and reachable?",
  reg:"Registry-attested — at least one fact comes from an official registry (RNS, Companies House, a regulator). It stands on its own without press coverage.",
  multi:"2+ sources — corroborated by two or more independent outlets.",
  single:"Single source — rests on one non-registry source. Treat with care until corroborated.",
  rt:"Stress-tested — a sceptical second pass tried to kill this lead and failed.",
  fee:"Fee event — a trigger that historically converts into a paid search mandate.",
  q4:"Budget-flush window — the company's year-end is close, so unspent budget converts into signed briefs.",
  prop:"Fee propensity — how likely this company is to pay an external search fee."
};
let mode='leads',filt='ready',sort='strength',view='engine';
let toastT=null,openerT=null;
function toast(m){const t=$('toast');t.innerHTML='<span class="sp">✦</span> '+esc(m);
  t.classList.add('show');clearTimeout(toastT);toastT=setTimeout(()=>t.classList.remove('show'),2600);}
function ageLabel(a){if(a==null||a==='')return '';return a===0?'today':a+'d ago';}
function scol(s){return s>=70?'#1E9E57':s>=45?'#D97A2B':'#8A94A6';}
function sband(s){return s>=70?'s-hi':s>=45?'s-md':'s-lo';}
function countUp(el,target){const t0=performance.now();
  (function f(t){const p=Math.min(1,(t-t0)/950),e=1-Math.pow(1-p,3);
    el.textContent=Math.round(target*e);if(p<1)requestAnimationFrame(f);})(t0);}

/* ---------- views ---------- */
function setView(v){
  view=v;
  $('v-engine').classList.toggle('on',view==='engine');
  $('v-cal').classList.toggle('on',view==='cal');
  $('v-shop').classList.toggle('on',view==='shop');
  $('vbEngine').classList.toggle('on',view==='engine');
  $('vbCal').classList.toggle('on',view==='cal');
  $('vbShop').classList.toggle('on',view==='shop');
  /* the dev-only footer belongs to the leads pages only */
  $('devFoot').style.display=(view==='engine')?'':'none';
}
$('vbEngine').addEventListener('click',()=>setView('engine'));
$('vbCal').addEventListener('click',()=>setView('cal'));
$('vbShop').addEventListener('click',()=>setView('shop'));

/* the daily stamp — UK date, regenerated on every load */
$('pipeDate').textContent=new Date().toLocaleDateString('en-GB',
  {weekday:'long',day:'numeric',month:'long',year:'numeric'}).toUpperCase();

/* ---------- engine stages + trigger pills ---------- */
const STAGES={
  leads:{slots:[1,2,3,4,5],
    lbl:['SEARCHED','FILTERED','BUILT OUT','STRESS-TESTED','READY LEADS'],
    cap:['Automated agent search for any hiring signal',
         'Back-end filters coded to pick up and pull senior seat signals for this desk',
         'Cross-source intelligence to turn signals into comprehensive leads',
         'Rigorous testing performed through the code, to minimise noise and cold calls',
         'Scored, ranked, and synthesised into brief dossier — ready for AD']},
  jobs:{slots:[1,2,4,5],
    lbl:['SEARCHED','FILTERED','VERIFIED','COMPILED AND READY'],
    cap:['Autonomous search of the internet for job vacancies',
         'Pull the listings relevant to VMA sectors',
         'Fits the back-end criteria to fit VMA business requirements.',
         'Organise and produce in sorted order - ready for outreach.']}};
function renderEngine(){
  const E=window.ENG||{};
  const isJobs=mode==='jobs';
  const nums=isJobs
    ?[E.j_scraped||0,E.j_filt||0,E.j_ver||0,E.j_live||0]
    :[E.gen||0,E.filt||0,E.coll||0,E.test||0,E.ready||0];
  const cfg=STAGES[mode];
  $('stagesEl').classList.toggle('jobs4',isJobs);
  /* jobs mode keeps the scene boxes down but counts like warm signals:
     a real, narrowing funnel from searched through to compiled */
  $('stagesEl').classList.toggle('noscene',isJobs);
  cfg.slots.forEach((slot,i)=>{
    countUp($('st'+slot),nums[i]||0);
    $('sl'+slot).textContent=cfg.lbl[i];
    $('cap'+slot).textContent=cfg.cap[i];
  });
  /* leads: trigger families loop inside the Filtered box;
     jobs: the boxes and numbers stand down — the job boards loop in the
     wide ticker above the sequence instead, drifting leftwards */
  $('jobsTick').style.display=isJobs?'':'none';
  if(isJobs){
    /* the tab streams source-style text built from the boards we search */
    const URLS=['linkedin.com/jobs','uk.indeed.com','reed.co.uk','totaljobs.com',
      'cwjobs.co.uk','jobs.theguardian.com','charityjob.co.uk',
      'civilservicejobs.service.gov.uk','s1jobs.com','glassdoor.co.uk',
      'monster.co.uk','jobsite.co.uk','escapethecity.org','otta.com',
      'welcometothejungle.com','workinstartups.com','jobs.prweek.com',
      'jobs.campaignlive.co.uk','jobs.marketingweek.com','exec-appointments.ft.com'];
    const frag=u=>'<td class="board"><a href="https://'+u+'/search?q=senior+communications">'+u+'</a></td> | ';
    const mid=Math.ceil(URLS.length/2);
    const l1=URLS.slice(0,mid).map(frag).join(''),l2=URLS.slice(mid).map(frag).join('');
    $('jt').innerHTML=esc(l1+l1+l1);
    $('jt2').innerHTML=esc(l2+l2+l2);
  }else{
    const items=window.TRIG||[];
    const half=Math.ceil(items.length/2);
    const mk=arr=>arr.map(t=>'<span class="tpill"><i></i>'+esc(t)+'</span>').join('');
    const r1=mk(items.slice(0,half)),r2=mk(items.slice(half));
    if($('m2r1'))$('m2r1').innerHTML=r1+r1+r1;
    if($('m2r2'))$('m2r2').innerHTML=(r2||r1)+(r2||r1)+(r2||r1);
  }
  requestAnimationFrame(sizeRail);
}
/* the rail runs number-centre to number-centre — never past the ready count */
function sizeRail(){
  const rail=document.querySelector('.rail');if(!rail)return;
  const stages=$('stagesEl');
  const vis=stages.querySelectorAll('.stg');
  let first=null,last=null;
  vis.forEach(s=>{if(s.offsetParent===null)return;if(!first)first=s;last=s;});
  if(!first||!last)return;
  const sb=stages.getBoundingClientRect();
  const fb=first.getBoundingClientRect(),lb=last.getBoundingClientRect();
  rail.style.left=(fb.left-sb.left+fb.width/2)+'px';
  rail.style.right=(sb.right-(lb.left+lb.width/2))+'px';
  /* anchor the rail to the numbers row — or the label row when the
     numbers are stood down (jobs mode) */
  const anchor=first.querySelector('.num:not([style*="display: none"])');
  const a=(anchor&&anchor.offsetParent!==null)?anchor:first.querySelector('.lbl');
  if(a){const ab=a.getBoundingClientRect();
    rail.style.top=(ab.top-sb.top+ab.height/2)+'px';}
}
window.addEventListener('resize',sizeRail);

/* ---------- the mini stage scenes ---------- */
(function(){
  /* m1: the crawl table — rows of the desk's real scan surface */
  const SRCS=[['londonstockexchange.com','/rns',788],['ft.com','/companies',771],
    ['prweek.com','/uk',794],['linkedin.com','/jobs',741],['companieshouse.gov.uk','/filings',752],
    ['reuters.com','/business',781],['ofwat.gov.uk','/enforcement',773],['marketingweek.com','/moves',766]];
  const rows=SRCS.map(s=>'<div class="crow"><span class="u">'+s[0]+'<b>'+s[1]+'</b></span>'
    +'<span class="ms2">'+s[2]+'ms</span></div>').join('');
  const ct=$('crawlTrack');if(ct)ct.innerHTML=rows+rows;
  /* shared ember strips — the per-cell churn engine, miniature */
  document.querySelectorAll('[data-ember]').forEach(box=>{
    const GLYPH=['.',':','-','=','+','·'],ROWS=4,COLS=46;
    const wisps=[];for(let i=0;i<9;i++)wisps.push({x:2+((i*COLS/9)|0)+((Math.random()*3)|0),w:1+(Math.random()<.5?1:0),h:1+((Math.random()*3)|0)});
    function hAt(c){let h=1;for(const w of wisps){if(c>=w.x&&c<w.x+w.w)h=Math.max(h,w.h);}return h;}
    let html='';for(let r=ROWS-1;r>=0;r--){for(let c=0;c<COLS;c++)html+='<span></span>';html+='\n';}
    box.innerHTML=html;
    const spans=box.querySelectorAll('span');
    const at=(r,c)=>spans[(ROWS-1-r)*COLS+c];
    setInterval(()=>{
      for(let k=0;k<26;k++){
        const r=(Math.random()*ROWS)|0,c=(Math.random()*COLS)|0,el=at(r,c);
        if(r>=hAt(c)){el.textContent=' ';continue;}
        if(Math.random()<.12){el.textContent=' ';}
        else{el.textContent=GLYPH[(Math.random()*GLYPH.length)|0];
          el.style.opacity=[.3,.55,.95][(Math.random()*3)|0];}
      }
    },110);
  });
  /* m5: the ASCII dissolve field from the BD Lead Radar landing, running
     through the whole box — same row-string mechanic and ' .:-=+X' density
     ramp, retuned for the light page (VMA navy at low alpha) */
  (function(){
    const cv=$('m5fx');if(!cv)return;
    const ctx=cv.getContext('2d');
    const dpr=Math.min(window.devicePixelRatio||1,2);
    const RAMP=' .:-=+X';
    const COLOR='rgba(62,92,132,0.34)';
    let W=0,H=0,CW=5,CH=10,cols=0,rows=0,phase=null;
    function build(){
      const r=cv.getBoundingClientRect();W=r.width;H=r.height;
      if(!W||!H)return;
      cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);
      ctx.setTransform(dpr,0,0,dpr,0,0);
      ctx.font='8px "JetBrains Mono",monospace';
      ctx.textBaseline='top';
      CW=ctx.measureText('X').width||4.8;
      CH=Math.round(CW*2.04);
      cols=Math.ceil(W/CW)+1;rows=Math.ceil(H/CH)+1;
      phase=new Float32Array(cols*rows);
      for(let i=0;i<phase.length;i++)phase[i]=Math.random()*6.2832;
    }
    function draw(t){
      if(!phase)return;
      ctx.clearRect(0,0,W,H);
      const ts=t*0.001;
      ctx.fillStyle=COLOR;
      for(let gy=0;gy<rows;gy++){
        const y=gy*CH,vy=y/H;
        let vert=(vy-0.05)/0.95;if(vert<0)vert=0;else if(vert>1)vert=1;vert*=vert;
        if(vert<=0.0015)continue;
        let row='';const base=gy*cols;
        for(let gx=0;gx<cols;gx++){
          const x=gx*CW,ph=phase[base+gx];
          let l1=Math.sin(x*0.05+ts*0.30)*Math.sin(y*0.06-ts*0.24+ph*0.05);if(l1<0)l1=0;
          let l2=Math.sin(x*0.09-ts*0.21)*Math.sin(y*0.10+ts*0.18);if(l2<0)l2=0;
          const tex=0.5+0.5*Math.sin(x*0.16+y*0.13+ts*0.90+ph*0.30);
          const tw=Math.sin(ts*12.0+ph);
          const b=(vert*(1.10*l1+0.70*l2+0.10*tex)+0.12*tw*vert)*1.25;
          let idx=(b*7)|0;if(idx<0)idx=0;else if(idx>6)idx=6;
          row+=RAMP.charAt(idx);
        }
        ctx.fillText(row,0,y);
      }
    }
    build();
    window.addEventListener('resize',build);
    (function frame(now){
      if(!document.hidden&&cv.offsetParent!==null)draw(now||0);
      requestAnimationFrame(frame);
    })(0);
  })();
  /* m3: structured output with live-scrambling values */
  const cb=$('codeBox');
  if(cb){
    const CH='abAZ09?*=!-/';
    const scr=n=>{let s='';for(let i=0;i<n;i++)s+=CH[(Math.random()*CH.length)|0];return s;};
    function paint(){
      cb.innerHTML='[ {<br>&nbsp;&nbsp;<span class="k">"account"</span>: <span class="v">"'+scr(9)+'"</span>,<br>'
        +'&nbsp;&nbsp;<span class="k">"signal"</span>: <span class="v">"'+scr(12)+'"</span>,<br>'
        +'&nbsp;&nbsp;<span class="k">"seat"</span>: <span class="v">"'+scr(7)+'"</span>,<br>'
        +'&nbsp;&nbsp;<span class="k">"score"</span>: <span class="v">'+((Math.random()*40+55)|0)+'</span> } ]';
      const st=$('scrTxt');if(st)st.textContent=scr(8)+'…';
    }
    paint();setInterval(paint,160);
  }
})();
setInterval(()=>{
  const all=document.querySelectorAll('.ptrows .tpill, #jt .tchip');
  if(!all.length)return;
  const i=(Math.random()*all.length)|0;
  all[i].classList.add('match');
  setTimeout(()=>all[i].classList.remove('match'),1500);
},1000);

/* ---------- board ---------- */
const FILTS=[['ready',{leads:'Ready Leads',jobs:'Live Jobs'}],['unc',{leads:'Uncategorised',jobs:'Uncategorised'}],
  ['new',{leads:'New today',jobs:'New today'}],['followed',{leads:'Followed up',jobs:'Followed up'}],
  ['dismissed',{leads:'Dismissed',jobs:'Dismissed'}]];
const SORTS={leads:[['strength','Strongest opportunity'],['window','Soonest window'],['new','Newest first']],
  jobs:[['new','Newest first'],['az','Company A–Z']]};
function renderCtrls(){
  $('filtmenu').innerHTML=FILTS.map(f=>'<button data-f="'+f[0]+'"'+(filt===f[0]?' class="on"':'')+'>'+f[1][mode]+'</button>').join('');
  $('filtLbl').textContent=FILTS.find(f=>f[0]===filt)[1][mode];
  $('sortmenu').innerHTML=SORTS[mode].map(s=>'<button data-s="'+s[0]+'"'+(sort===s[0]?' class="on"':'')+'>'+s[1]+'</button>').join('');
  $('sortLbl').textContent=SORTS[mode].find(s=>s[0]===sort)[1].split(' ')[0];
}
function chip(cls,txt,tip){return '<span class="chip '+cls+'"'+(tip?' data-tip="'+esc(tip)+'"':'')+'>'+txt+'</span>';}
function miniArc(score){
  const C=2*Math.PI*11,d=(C*(score||0)/100).toFixed(1);
  return '<svg width="28" height="28" viewBox="0 0 28 28">'
    +'<circle cx="14" cy="14" r="11" fill="none" stroke="rgba(16,22,38,.09)" stroke-width="2.5"/>'
    +'<circle cx="14" cy="14" r="11" fill="none" stroke="'+scol(score||0)+'" stroke-width="2.5" '
    +'stroke-linecap="round" stroke-dasharray="'+d+' '+C.toFixed(1)+'" transform="rotate(-90 14 14)"/></svg>';
}
function srcKind(src){
  src=(src||'').toLowerCase();
  if(/londonstockexchange|companieshouse|ofwat|ofgem|fca\.org|gov\.uk|sec\.gov/.test(src))return 'REGISTRY';
  if(/linkedin/.test(src))return 'SOCIAL';
  return 'PRESS';
}
function portfolioHTML(l){
  const score=l.score==null?0:l.score;
  const C=2*Math.PI*26,dash=(C*score/100).toFixed(1),col=scol(score);
  const q=l.qual||{};
  const ready=(l.tier||'ready')==='ready';
  const srcs=(l.stack&&l.stack.length)?l.stack:(l.url?[{label:l.brief||l.why||l.co,src:l.src||'',url:l.url,age:l.age}]:[]);
  let h='<div class="port" data-qa="portfolio">';
  h+='<div class="port-top"><div>'
    +'<div class="eyeb">BUSINESS LEAD PORTFOLIO · VMA GROUP</div>'
    +'<div class="pco">'+esc(l.co)+'</div>'
    +'<div class="pmand"><b>'+esc(l.seat||'')+'</b>'+(l.why?' · '+esc(l.why):'')
    +(ageLabel(l.age)?' · signal '+ageLabel(l.age):'')+'</div></div>'
    +'<div class="pright"><div class="pchips">'
    +(l.ver?chip('ver-'+l.ver,esc(VER_LABEL[l.ver]||l.ver).toUpperCase(),MEANING[l.ver]):'')
    +(l.rt?chip('rt','STRESS-TESTED ✓ '+(l.conviction||''),MEANING.rt+(l.conviction?' Conviction '+l.conviction+'/100.':'')):'')
    +(l.conflict?chip('anti','COMPETING RECRUITER','A rival search firm already holds a mandate here — do not call.'):'')
    +'</div>'
    +'<div class="scorering"><svg width="62" height="62" viewBox="0 0 62 62">'
    +'<circle cx="31" cy="31" r="26" fill="none" stroke="rgba(16,22,38,.08)" stroke-width="3.5"/>'
    +'<circle cx="31" cy="31" r="26" fill="none" style="stroke:'+col+'" stroke-width="3.5" '
    +'stroke-linecap="round" stroke-dasharray="'+dash+' '+C.toFixed(1)+'" transform="rotate(-90 31 31)"/>'
    +'</svg><span class="sv" style="color:'+col+'">'+score+'</span></div></div></div>';
  if(!ready&&l.gateWhy){
    h+='<div class="prow"><span class="pl2">WHY NOT CALL-READY</span><div class="pv"><div class="gatebox">'+esc(l.gateWhy)+'</div></div></div>';
  }
  if(l.whyNow||l.brief)h+='<div class="prow"><span class="pl2">OPPORTUNITY</span><div class="pv">'+esc(l.whyNow||l.brief)+'</div></div>';
  h+='<div class="prow alt"><span class="pl2">MANDATE</span><div class="pv"><b>'+esc(l.seat||'')+'</b>'
    +(l.win?' · decision window '+esc(l.win):'')
    +(l.fee?' &nbsp;'+chip('fee',esc(l.fee),MEANING.fee+' '+(l.feeTip||'')):'')
    +(l.q4?' '+chip('q4',esc(l.q4),MEANING.q4):'')+'</div></div>';
  if(l.buyer)h+='<div class="prow"><span class="pl2">DECISION MAKER</span><div class="pv"><b>'+esc(l.buyer)+'</b></div></div>';
  if(l.champion)h+='<div class="prow alt"><span class="pl2">ROUTE IN</span><div class="pv">'+esc(l.champion)+'</div></div>';
  if(l.opening||l.move)h+='<div class="prow"><span class="pl2">OPENING LINE</span><div class="pv">'+esc(l.opening||l.move)+'</div></div>';
  if(l.kill)h+='<div class="prow alt"><span class="pl2">RISK</span><div class="pv">'+esc(l.kill)+'</div></div>';
  if(srcs.length){
    h+='<div class="prow"><span class="pl2">SOURCES</span><div class="pv">'
      +srcs.map(s=>'<a class="srcl" href="'+esc(s.url||('https://'+(s.src||'')))+'" target="_blank" rel="noopener">'
      +'<span class="ek">'+srcKind(s.src||s.url)+'</span>'
      +'<span class="el2">'+esc(s.label||s.src||'source')+'</span>'
      +'<span class="ed2">'+esc(s.src||'')+(ageLabel(s.age)?' · '+ageLabel(s.age):'')+'</span>'
      +'<svg class="ext" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/></svg></a>').join('')
      +'</div></div>';
  }
  if(q.total!=null){
    h+='<div class="prow alt"><span class="pl2">QUALIFICATION</span><div class="pv"><div class="qmarks">'
      +[['Seat',q.seat,MEANING.seat,q.seat_why],['Budget',q.budget,MEANING.budget,q.budget_why],
        ['Urgency',q.urgency,MEANING.urgency,q.urgency_why],['Buyer',q.buyer,MEANING.buyer,q.buyer_why]]
        .map(x=>'<span class="qmark" data-tip="'+esc(x[2])+(x[3]?' Here: '+esc(x[3])+'.':'')+'">'+x[0]+' <b style="color:'
        +(x[1]>=2?'#1E7A41':x[1]===1?'#B45309':'#9AA0A6')+'">'+(x[1]>=2?'✓✓':x[1]===1?'✓':'✗')+'</b></span>').join('')
      +'<span class="qmark"><b>'+q.total+'/8</b></span>'
      +(l.prop?chip('prop',esc(l.prop).toUpperCase(),MEANING.prop+(l.propWhy?' Here: '+l.propWhy+'.':'')):'')
      +'</div></div></div>';
  }
  if(l.bizCase)h+='<div class="prow"><span class="pl2">FEE BASIS</span><div class="pv"><b>'+esc(l.bizCase)+'</b></div></div>';
  h+='<div class="openerbox" id="ob-'+l._id+'" hidden></div>';
  h+='<div class="port-foot">'
    +'<button type="button" class="btn primary" data-act="pitch" data-id="'+l._id+'">'
    +'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M13.5 3v5.5H19"/><path d="M9.2 13l.55 1.65 1.65.55-1.65.55L9.2 17.4l-.55-1.65L7 15.2l1.65-.55z" fill="currentColor" stroke="none"/></svg>'
    +'Generate pitch</button>'
    +(l.opener?'<button type="button" class="btn" data-act="opener" data-id="'+l._id+'">✦ Draft opener</button>':'')
    +'<span class="gap"></span>'
    +'<button type="button" class="btn good'+(l.status==='followed_up'?' on':'')+'" data-act="fu" data-id="'+l._id+'">✓ Followed up</button>'
    +'<button type="button" class="btn bad'+(l.status==='dismissed'?' on':'')+'" data-act="dis" data-id="'+l._id+'">✕ Dismiss</button>'
    +'</div></div>';
  return h;
}
function stripHTML(l,i){
  const cls=(l.status==='followed_up'?' done-fu':'')+(l.status==='dismissed'?' done-dis':'');
  return '<div class="strip'+cls+'" data-id="'+l._id+'">'
    +'<div class="strip-h" tabindex="0" data-qa="lead-trigger" data-id="'+l._id+'">'
    +'<span class="rank">'+String(i+1).padStart(2,'0')+'</span>'
    +'<div class="idcell"><div class="co"><span class="nm">'+esc(l.co)+'</span>'
    +(l.isNew?'<span class="tag-new">NEW</span>':'')
    +(l.conflict?'<span class="tag-dnc">DO NOT CALL</span>':'')+'</div>'
    +'<div class="seat">'+esc(l.seat||'')+'</div></div>'
    +'<span class="tp '+(l.key||'lead')+'">'+esc(l.type||'Signal')+'</span>'
    +'<span class="wincell">'+esc((l.win||'').toUpperCase())+'</span>'
    +'<span class="strengthcell '+sband(l.score||0)+'"><span class="sn">'+(l.score==null?'—':l.score)+'</span>'+miniArc(l.score)+'</span>'
    +'<span class="chev"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg></span>'
    +'</div><div class="dossier">'+portfolioHTML(l)+'</div></div>';
}
function jobRow(l,i){
  const cls=(l.status==='followed_up'?' done-fu':'')+(l.status==='dismissed'?' done-dis':'');
  return '<div class="jrow'+cls+'" data-url="'+esc(l.url||'')+'" title="Open posting">'
    +'<span class="rank">'+String(i+1).padStart(2,'0')+'</span>'
    +'<div><div class="jt">'+esc(l.jt||'')+'</div><div class="jc">'+esc(l.co)+'</div></div>'
    +'<span class="srcpills">'+srcPills(l)+'</span>'
    +'<span class="geo">'+esc((l.geo||'').toUpperCase())+'</span>'
    +'<span class="age2">'+(l.isNew?'NEW':'')+'</span>'
    +'<span class="jacts">'
    +'<button type="button" class="jact fu'+(l.status==='followed_up'?' on':'')+'" data-jtri="followed_up" data-id="'+l._id+'" title="Followed up">'
    +'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg></button>'
    +'<button type="button" class="jact dis'+(l.status==='dismissed'?' on':'')+'" data-jtri="dismissed" data-id="'+l._id+'" title="Dismiss">'
    +'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>'
    +'</span>'
    +'<span class="lnk"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/></svg></span></div>';
}
/* aggregator sources unpack into the boards they pull from — one pill
   each; the aggregator's own name (Adzuna) is never shown */
function srcPills(l){
  const raw=l.sourceRaw||l.source||'';
  let names=[];
  const m=raw.match(/^([^(]+)\(([^)]+)\)/);
  if(m){
    const base=m[1].trim();
    names=m[2].split(/[+,/&]/).map(s=>s.trim())
      .filter(s=>s&&!/aggregator|public|network|other|adzuna/i.test(s));
    if(base&&!/adzuna/i.test(base))names.unshift(base);
  }else if(raw){names=[raw.trim()];}
  names=names.filter(n=>n&&!/adzuna/i.test(n));
  if(!names.length&&l.source&&!/adzuna/i.test(l.source))names=[l.source];
  return names.slice(0,3).map(n=>'<span class="srcpill">'+esc(n)+'</span>').join('');
}
function winWeeks(s){s=(''+(s||'')).toLowerCase();const m=s.match(/(\d+)/);if(!m)return 9999;
  const n=+m[1];return /mo|month/.test(s)?n*4.33:n;}
function statusFilter(l){
  if(filt==='ready')return l.status!=='dismissed';
  if(filt==='unc')return l.status==='active';
  if(filt==='new')return l.isNew&&l.status!=='dismissed';
  if(filt==='followed')return l.status==='followed_up';
  if(filt==='dismissed')return l.status==='dismissed';
  return true;
}
function renderBoard(){
  const isLeads=mode==='leads';
  $('blToggle').style.display=isLeads?'':'none';
  $('blList').classList.remove('on');$('blToggle').classList.remove('on');
  renderCtrls();
  if(!isLeads){
    let jobs=JOBS.filter(statusFilter);
    if(sort==='az')jobs.sort((a,b)=>(a.co||'').localeCompare(b.co||''));
    else jobs.sort((a,b)=>(b.isNew?1:0)-(a.isNew?1:0)||(a.co||'').localeCompare(b.co||''));
    $('strips').innerHTML=jobs.length?jobs.map((j,i)=>jobRow(j,i)).join('')
      :'<div style="padding:26px 4px;font-size:12.5px;color:var(--dim)">Nothing under this filter.</div>';
    $('blLabel').textContent='';$('blList').innerHTML='';
    return;
  }
  /* blocked leads (competing recruiter, freeze, administration) never
     surface — the engine simply doesn't show them */
  const readyAll=BD.filter(l=>(l.tier||'ready')==='ready'&&!l.conflict);
  const rest=BD.filter(l=>l.tier==='dev'||l.tier==='early');
  let ready=readyAll.filter(statusFilter);
  if(sort==='window')ready.sort((a,b)=>winWeeks(a.win)-winWeeks(b.win)||(b.score||0)-(a.score||0));
  else if(sort==='new')ready.sort((a,b)=>((a.age===''?99:a.age)??99)-((b.age===''?99:b.age)??99)||(b.score||0)-(a.score||0));
  else ready.sort((a,b)=>(b.score||0)-(a.score||0));
  $('strips').innerHTML=ready.length?ready.map((l,i)=>stripHTML(l,i)).join('')
    :'<div style="padding:26px 4px;font-size:12.5px;color:var(--dim)">Nothing under this filter.</div>';
  const nDev=rest.filter(l=>l.tier==='dev').length,
        nEarly=rest.filter(l=>l.tier==='early').length;
  $('blLabel').textContent='Below the line — '+nDev+' developing · '+nEarly+' watch';
  $('blList').innerHTML=rest.map(l=>'<div class="bl-row'+(l.tier==='blocked'?' blocked':'')+'">'
    +'<span class="rank">·</span>'
    +'<div class="co2"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(l.co)+'</span>'
    +(l.tier==='blocked'||l.conflict?'<span class="tag-dnc">DO NOT CALL</span>':'')
    +'<span style="font:600 9px var(--mono);letter-spacing:.1em;color:var(--dim)">'+(TIER_WORD[l.tier]||'').toUpperCase()+'</span></div>'
    +'<span class="tp '+(l.key||'lead')+'">'+esc(l.type||'Signal')+'</span>'
    +'<span class="gw">'+esc(l.gateWhy||'')+'</span></div>').join('');
}
function draftOpener(id){
  const l=BYID[id],box=$('ob-'+id);if(!l||!l.opener||!box)return;
  box.hidden=false;box.scrollIntoView({behavior:'smooth',block:'nearest'});
  if(box.dataset.done){box.textContent=l.opener;return;}
  box.dataset.done='1';let i=0;box.textContent='';
  (function tick(){i+=2;box.textContent=l.opener.slice(0,i);
    if(i<l.opener.length)openerT=setTimeout(tick,14);})();
}
/* live triage — same endpoint routing as the legacy console */
function triage(id,st){
  const l=BYID[id];if(!l)return;
  const prev=l.status;
  l.status=(l.status===st)?'active':st;
  renderBoard();
  toast(l.co+(l.status==='dismissed'?' dismissed':l.status==='followed_up'?' marked followed up':' restored'));
  let url,body;
  if(l.idtype==='funding'){url='/api/funding/mark';body={fid:l.rid,status:l.status};}
  else if(l.idtype==='job'||l.jt!==undefined){url='/api/lead/'+encodeURIComponent(l.rid)+'/status';body={status:l.status};}
  else{url='/api/predictor/'+encodeURIComponent(l.rid)+'/status';body={status:l.status};}
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(r=>r.json()).then(j=>{if(!j||j.ok===false){l.status=prev;toast('Could not save status');renderBoard();}})
    .catch(()=>{l.status=prev;renderBoard();});
}
/* live pitch dispatch from the portfolio */
function pitchFromLead(id,btn){
  const l=BYID[id];if(!l||btn.classList.contains('busy'))return;
  btn.classList.add('busy');
  const win=window.open('','_blank');
  if(win){win.document.write('<!doctype html><meta charset="utf-8"><title>Preparing pitch pack…</title><style>body{font-family:Inter,system-ui,sans-serif;background:#f7f9fc;color:#1F1F1F;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}.b{text-align:center;padding:24px}.s{width:30px;height:30px;border:3px solid rgba(66,133,244,.2);border-top-color:#4285F4;border-radius:50%;margin:0 auto 16px;animation:r .8s linear infinite}@keyframes r{to{transform:rotate(360deg)}}p{font-size:13px;color:#5F6368;max-width:340px;line-height:1.55}</style><div class="b"><div class="s"></div><h3>Preparing the pitch pack…</h3><p>A few minutes. Keep this tab open — it loads automatically when ready.</p></div>');}
  fetch('/api/dispatch/pitch-pack',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({account_name:l.co,role:l.pitchRole||l.seat||'',trigger:l.pitchTrigger||''})})
    .then(r=>r.json()).then(j=>{
      if(!j||!j.ok||!j.artifact||!j.dispatched_at){if(win&&!win.closed)win.close();btn.classList.remove('busy');toast((j&&j.detail)||'Pitch dispatch failed');return;}
      toast('Generating pitch pack for '+l.co+'…');
      const qs='artifact='+encodeURIComponent(j.artifact)+'&since='+encodeURIComponent(j.dispatched_at);
      const started=Date.now(),MAX=25*60*1000;
      (function poll(){
        if(Date.now()-started>MAX){if(win&&!win.closed)win.close();btn.classList.remove('busy');toast('Pitch pack timed out');return;}
        fetch('/api/output/status?'+qs).then(r=>r.json()).then(s=>{
          if(s&&s.ready&&s.id){const v='/api/output/view?artifact='+encodeURIComponent(j.artifact)+'&id='+encodeURIComponent(s.id);
            if(win&&!win.closed)win.location=v;btn.classList.remove('busy');toast('Pitch pack ready');}
          else setTimeout(poll,12000);
        }).catch(()=>setTimeout(poll,12000));
      })();
    }).catch(()=>{if(win&&!win.closed)win.close();btn.classList.remove('busy');toast('Pitch dispatch failed');});
}

/* ---------- calendar (live pulses / events / frameworks) ---------- */
function monthIdx(dstr){
  if(!dstr)return -1;
  const d=new Date(dstr);if(isNaN(d))return -1;
  const M=window.CAL_MONTHS||[];
  for(let i=0;i<M.length;i++){if(d.getFullYear()===M[i].y&&d.getMonth()===M[i].m)return i;}
  return -1;
}
function fmtD(dstr){const d=new Date(dstr);if(isNaN(d))return '';
  return String(d.getDate()).padStart(2,'0')+' '+['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'][d.getMonth()];}
function calItems(){
  const ev=(window.CAL_EVENTS||[]).map((e,i)=>({kind:'ev',key:'ev'+i,
    n:e.name||e.title||'',m:monthIdx(e.event_date||e.date),d:fmtD(e.event_date||e.date),
    loc:e.location||'',why:e.why_now||e.why||'',url:e.source||e.url||'',open:0}));
  const wi=(window.CAL_PULSES||[]).map((w,i)=>{
    const aw=Array.isArray(w.window)?w.window:(Array.isArray(w.action_window)?w.action_window:[]);
    const a=w.window_start||w.start||aw[0]||null;
    const b=w.window_end||w.end||w.deadline||w.legal_date||aw[1]||null;
    const open=(w.days_left!=null&&w.days_left>=0)||w.status==='open'||w.open
      ||(a&&b&&new Date(a)<=new Date()&&new Date()<=new Date(b));
    let m=monthIdx(a);if(m<0)m=open?0:monthIdx(b);if(m<0)m=0;
    const why=[w.angle||w.why_now||w.why||w.evidence||'',
               w.seat?('The seat: '+w.seat):'',
               w.scope_note||''].filter(Boolean).join(' — ');
    /* the popover carries the window's actual opening and closing dates */
    const dates=(a?('Opens '+fmtD(a)):'Already open')+(b?(' · Closes '+fmtD(b)):'');
    return {kind:'wi',key:'wi'+i,n:w.name||w.title||'',m:m,a:a,b:b,dates:dates,
      d:b?('→ '+fmtD(b)):(w.days_left!=null?w.days_left+'D LEFT':'OPEN'),
      loc:'',why:why,url:w.source_url||w.url||'',open:open?1:0};});
  const fw=(window.CAL_FW||[]).map((f,i)=>{
    const open=(f.status==='live'||f.status==='refresh_window'||f.in_window);
    const a=f.window_start||f.refresh_date||f.start||null;
    let m=monthIdx(a);if(m<0)m=0;
    return {kind:'fw',key:'fw'+i,n:f.title||f.name||'',m:m,
      d:open?'OPEN':(a?('FROM '+fmtD(a)):(f.status||'').toUpperCase().slice(0,12)),
      loc:f.buyer||'',why:f.note||f.why||('Buyer: '+(f.buyer||'')),
      url:f.portal||f.url||f.source||'',open:open?1:0};});
  return {ev,wi,fw};
}
function renderCal(){
  const M=window.CAL_MONTHS||[];
  $('cgHead').innerHTML='<span></span>'+M.map((m,i)=>'<span'+(i===0?' class="today" data-td="TODAY · '+m.td+'"':'')+'>'+m.lbl+'</span>').join('');
  const it=calItems();
  function rowHTML(label,colour,count,items){
    let cells='';
    for(let m=0;m<7;m++){
      cells+='<div class="cg-cell">'
        +items.filter(x=>x.m===m).map(x=>'<span class="cpill '+x.kind+(x.open?' openg':'')+'" data-cal="'+x.key+'">'
          +'<i></i><span class="n">'+esc(x.n)+'</span><span class="d">'+esc(x.d)+'</span></span>').join('')
        +'</div>';
    }
    return '<div class="cg-row"><div class="cg-lab"><span class="t" style="color:'+colour+'">'+label+'</span>'
      +'<span class="c">'+count+'</span></div>'+cells+'</div>';
  }
  /* the windows row: bars that start and end exactly where the window does */
  function winRow(items){
    const start=new Date(M[0].y,M[0].m,1).getTime();
    const end=new Date(M[6].y,M[6].m+1,0).getTime();
    const pct=ds=>{const d=new Date(ds).getTime();
      return Math.max(0,Math.min(100,(d-start)/(end-start)*100));};
    const lanes=[];
    const bars=items.slice().sort((x,y)=>pct(x.a||0)-pct(y.a||0)).map(x=>{
      let l=x.a?pct(x.a):0;if(!isFinite(l))l=0;
      const pe=x.b?pct(x.b):NaN;
      const r=isFinite(pe)?Math.max(l+7,pe):Math.min(100,l+12);
      let lane=0;
      while(lane<lanes.length&&l<lanes[lane]+1.5)lane++;
      lanes[lane]=r;
      return '<span class="wspan'+(x.open?' openg':'')+'" data-cal="'+x.key+'" '
        +'style="left:'+l.toFixed(2)+'%;width:'+(r-l).toFixed(2)+'%;top:'+(9+lane*30)+'px">'
        +'<i></i><span class="n">'+esc(x.n)+'</span><span class="d">'+esc(x.d)+'</span></span>';
    }).join('');
    const h=Math.max(1,lanes.length)*30+16;
    let lines='';for(let i=1;i<7;i++)lines+='<span class="mline" style="left:'+(i*100/7).toFixed(3)+'%"></span>';
    return '<div class="cg-row"><div class="cg-lab"><span class="t" style="color:#0e7c74">WINDOWS</span>'
      +'<span class="c">'+items.filter(x=>x.open).length+' open</span></div>'
      +'<div class="wtrack" style="min-height:'+h+'px">'+lines+bars+'</div></div>';
  }
  $('cgRows').innerHTML=
    rowHTML('EVENTS','#1d4ed8',it.ev.length+' dated',it.ev)
    +winRow(it.wi)
    +rowHTML('FRAMEWORKS','#6b3fb5',it.fw.length+' tracked',it.fw);
  window._calLookup={};
  [].concat(it.ev,it.wi,it.fw).forEach(x=>{window._calLookup[x.key]=x;});
}
const pop=document.getElementById('calPop');

/* ---------- workshop chips (live PA morph behaviour) ---------- */
const composer=$('composer');
function setCap(cap){
  const cur=composer.getAttribute('data-mode');
  const next=(cur===cap)?'free':cap;
  composer.setAttribute('data-mode',next);
  document.querySelectorAll('.cap-form').forEach(f=>f.classList.toggle('active',f.dataset.cap===next));
  document.querySelectorAll('.shopchip').forEach(c=>c.classList.toggle('active',c.dataset.cap===next));
  $('cprompt').style.display=(next==='free')?'':'none';
  if(next==='free')$('cprompt').focus();
}
$('shopChips').addEventListener('click',e=>{
  const c=e.target.closest('.shopchip');if(c)setCap(c.dataset.cap);
});
$('composerSend').addEventListener('click',()=>{
  const v=$('cprompt').value.trim();
  if(!v){toast('Tell the workshop what to make — or pick a deck below');$('cprompt').focus();return;}
  /* free-text router: nudge towards the matching deck */
  const s=v.toLowerCase();
  if(/pitch/.test(s))setCap('pitch');
  else if(/match|candidate/.test(s))setCap('reverse');
  else if(/meet|brief/.test(s))setCap('premeeting');
  else if(/sweep|scan/.test(s))setCap('sweep');
  else toast('Pick a deck below and the workshop will build it');
});
$('cprompt').addEventListener('keydown',e=>{if(e.key==='Enter')$('composerSend').click();});

/* ---------- global events ---------- */
document.addEventListener('click',e=>{
  const ms=e.target.closest('#modeSeg button');
  if(ms&&ms.dataset.m!==mode){mode=ms.dataset.m;
    document.querySelectorAll('#modeSeg button').forEach(x=>x.classList.toggle('on',x===ms));
    filt='ready';sort=(mode==='leads')?'strength':'new';
    renderEngine();renderBoard();return;}
  const mb=e.target.closest('[data-menu]');
  if(mb){const m=$(mb.dataset.menu);
    document.querySelectorAll('.ctrlmenu.open').forEach(x=>{if(x!==m)x.classList.remove('open');});
    m.classList.toggle('open');e.stopPropagation();return;}
  const fb=e.target.closest('#filtmenu button');
  if(fb){filt=fb.dataset.f;$('filtmenu').classList.remove('open');renderBoard();return;}
  const sb=e.target.closest('#sortmenu button');
  if(sb){sort=sb.dataset.s;$('sortmenu').classList.remove('open');renderBoard();return;}
  document.querySelectorAll('.ctrlmenu.open').forEach(x=>x.classList.remove('open'));
  const jt=e.target.closest('[data-jtri]');
  if(jt){e.stopPropagation();triage(jt.dataset.id,jt.dataset.jtri);return;}
  const jr=e.target.closest('.jrow');
  if(jr){if(jr.dataset.url)window.open(jr.dataset.url,'_blank','noopener');return;}
  const cal=e.target.closest('[data-cal]');
  if(cal){
    /* clicking the same pill again closes the popup; the open pill is the
       only one with a solid-white background */
    if(pop.classList.contains('on')&&pop.dataset.key===cal.dataset.cal){
      pop.classList.remove('on');pop.dataset.key='';
      document.querySelectorAll('.cpill.sel,.wspan.sel').forEach(x=>x.classList.remove('sel'));
      e.stopPropagation();return;}
    pop.dataset.key=cal.dataset.cal;
    document.querySelectorAll('.cpill.sel,.wspan.sel').forEach(x=>x.classList.remove('sel'));
    cal.classList.add('sel');
    const it=(window._calLookup||{})[cal.dataset.cal];if(!it)return;
    const kindLbl={ev:'EVENT',wi:'PLACEMENT WINDOW',fw:'FRAMEWORK'}[it.kind];
    const kc={ev:'#1d4ed8',wi:'#0e7c74',fw:'#6b3fb5'}[it.kind];
    pop.innerHTML='<div class="pk" style="color:'+kc+'">'+kindLbl+'</div>'
      +'<div class="pn">'+esc(it.n)+'</div><div class="pd">'+esc(it.dates||it.d)+(it.loc?' · '+esc(it.loc):'')+'</div>'
      +(it.why?'<div class="pwl">WHY IT MATTERS</div><div class="pw">'+esc(it.why)+'</div>':'')
      +(it.url?'<a class="psrc" href="'+esc(it.url)+'" target="_blank" rel="noopener">View source '
      +'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/></svg></a>':'');
    const wrap=document.querySelector('.calwrap').getBoundingClientRect(),bb=cal.getBoundingClientRect();
    let x=bb.left-wrap.left;x=Math.max(8,Math.min(x,wrap.width-312));
    pop.style.left=x+'px';pop.style.top=(bb.bottom-wrap.top+10)+'px';
    pop.classList.add('on');e.stopPropagation();return;
  }
  if(!e.target.closest('.cal-pop')){pop.classList.remove('on');pop.dataset.key='';
    document.querySelectorAll('.cpill.sel,.wspan.sel').forEach(x=>x.classList.remove('sel'));}
  const act=e.target.closest('[data-act]');
  if(act&&act.dataset.id){
    const id=act.dataset.id,a=act.dataset.act;
    if(a==='pitch')pitchFromLead(id,act);
    else if(a==='opener')draftOpener(id);
    else if(a==='fu')triage(id,'followed_up');
    else if(a==='dis')triage(id,'dismissed');
    return;
  }
  const h=e.target.closest('.strip-h');
  if(h){
    const strip=h.closest('.strip'),was=strip.classList.contains('open');
    document.querySelectorAll('.strip.open').forEach(s=>s.classList.remove('open'));
    if(!was)strip.classList.add('open');
    return;
  }
});
document.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&document.activeElement&&document.activeElement.classList.contains('strip-h'))
    document.activeElement.click();
  if(e.key==='Escape'){document.querySelectorAll('.strip.open').forEach(s=>s.classList.remove('open'));
    pop.classList.remove('on');
    document.querySelectorAll('.ctrlmenu.open').forEach(x=>x.classList.remove('open'));}
});
document.querySelectorAll('#blToggle').forEach(b=>b.addEventListener('click',()=>{
  b.classList.toggle('on');$('blList').classList.toggle('on');}));

renderEngine();renderBoard();renderCal();
})();
</script>
</body>
</html>"""
