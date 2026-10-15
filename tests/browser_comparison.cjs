// Optional browser check using a generated reviewed report (no source data is modified).
const {chromium}=require(process.env.METRO_PLAYWRIGHT_DIR || 'playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const cwd=path.resolve(__dirname,'..'),output=path.resolve(process.env.METRO_COMPARISON_REPORT || 'results_reviewed');
 const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'metro-comparison-'));
 const port=process.env.METRO_COMPARISON_PORT || '8778';
 const server=spawn(process.env.METRO_PYTHON || 'python',['-m','metro_braking','--serve','--output',output,'--review-dir',path.join(temporary,'reviews'),'--port',port],{cwd});
 let logs='',browser;server.stdout.on('data',x=>logs+=x);server.stderr.on('data',x=>logs+=x);
 try {
  for(let i=0;i<100&&!logs.includes('Review stops at');i++) {
   if(server.exitCode!==null)throw Error(logs);
   await new Promise(r=>setTimeout(r,100));
  }
  assert(logs.includes('Review stops at'),logs);
  browser=await chromium.launch({headless:true,executablePath:process.env.METRO_BROWSER_EXE});
  const page=await browser.newPage({viewport:{width:1440,height:1100},deviceScaleFactor:1});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}/report.html`);
  const section=page.locator('#run-comparisons'),select=section.getByLabel('Interstation to compare');await select.waitFor();
  const payload=JSON.parse(await page.locator('#review-data').textContent());
  const expected=payload.trips.filter(t=>t.metrics.duration_eligible && t.metrics.coverage==='complete' && t.metrics.line==='4' && t.metrics.origin_station==='Châtelet' && t.metrics.destination_station==='Cité');
  assert(expected.length>=2,'Example needs at least two reviewed Châtelet – Cité runs');
  const choose=()=>select.selectOption(JSON.stringify(['4','Châtelet','Cité']));await choose();
  const chart=section.locator('.run-comparison-chart'),curves=chart.locator('polyline[data-recording]');
  assert.deepEqual((await curves.evaluateAll(nodes=>nodes.map(n=>n.dataset.recording))).sort(),expected.map(t=>t.metrics.recording).sort());
  const maximum=Math.max(...expected.map(t=>t.metrics.end_s-t.metrics.restart_s));
  assert(Math.abs(Number(await chart.getAttribute('data-view-end'))-maximum)<1e-8);
  const firstCurve=curves.first();const beforePoints=await firstCurve.getAttribute('points');assert(beforePoints.split(' ').length>100);
  const toggle=section.getByRole('checkbox').first();await toggle.uncheck();assert.equal(await curves.count(),expected.length-1);await toggle.check();assert.equal(await curves.count(),expected.length);
  await section.getByRole('button',{name:'First 30 s',exact:true}).click();assert.equal(Number(await chart.getAttribute('data-view-end')),30);
  await section.getByRole('button',{name:'Zoom in',exact:true}).click();assert(Math.abs(Number(await chart.getAttribute('data-view-end'))-Number(await chart.getAttribute('data-view-start'))-15)<1e-8);
  const from=section.getByLabel('From (s)',{exact:true}),to=section.getByLabel('To (s)',{exact:true});
  await from.fill('5');await to.fill('10');await to.press('Tab');assert.equal(Number(await chart.getAttribute('data-view-start')),5);assert.equal(Number(await chart.getAttribute('data-view-end')),10);
  await section.getByRole('button',{name:'Full runs',exact:true}).click();
  await chart.scrollIntoViewIfNeeded();const box=await chart.boundingBox();
  await page.mouse.move(box.x+box.width*.25,box.y+box.height*.5);await page.mouse.down();await page.mouse.move(box.x+box.width*.55,box.y+box.height*.5,{steps:5});await page.mouse.up();
  assert(Number(await chart.getAttribute('data-view-end'))-Number(await chart.getAttribute('data-view-start'))<maximum*.6);
  await chart.focus();await page.keyboard.press('Home');assert.equal(Number(await chart.getAttribute('data-view-start')),0);
  const link=section.locator('.comparison-legend-row a').first();const target=await link.getAttribute('href');await link.click();assert.equal(await page.locator(target).getAttribute('open'),'');
  // Isolate grouping rules and unknown boundary exclusions with distinct fixtures.
  await page.evaluate(()=>{
   const data=JSON.parse(document.getElementById('review-data').textContent);
   const base=data.trips.find(t=>t.metrics.duration_eligible);
   const make=(name,line,origin,destination,extra={})=>({blocks:base.blocks,metrics:{...base.metrics,recording:name,line,origin_station:origin,destination_station:destination,...extra}});
   window.renderRunComparisons([
    make('forward1','4','A','B'),make('forward2','4','A','B'),
    make('reverse1','4','B','A'),make('reverse2','4','B','A'),
    make('other-line1','8','A','B'),make('other-line2','8','A','B'),
    make('partial','4','A','B',{coverage:'departure_not_identified',restart_s:null}),
    make('unassigned','4',null,null,{duration_eligible:false})]);
  });
  assert.equal(await select.locator('option').count(),3);
  await select.selectOption(JSON.stringify(['4','A','B']));assert.equal(await curves.count(),2);
  await page.evaluate(()=>window.renderRunComparisons([]));assert((await section.textContent()).includes('No repeated complete'));
  // The normal rendering entry point also refreshes comparisons after an export.
  await page.evaluate(()=>window.renderTripCharts(JSON.parse(document.getElementById('review-data').textContent).trips));await choose();
  if(process.env.METRO_COMPARISON_SCREENSHOT) {
   const file=path.resolve(process.env.METRO_COMPARISON_SCREENSHOT);fs.mkdirSync(path.dirname(file),{recursive:true});
   await section.screenshot({path:file});
  }
  assert.deepEqual(errors,[]);
  console.log(`PASS: ${expected.length} Châtelet – Cité curves, time zoom, toggles, links, direction/line grouping, partial exclusion, empty state and refresh; no JavaScript errors.`);
 } finally {
  if(browser)await browser.close();
  if(server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}
  fs.rmSync(temporary,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
