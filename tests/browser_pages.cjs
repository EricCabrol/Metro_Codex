// Optional Pages smoke test; only a static HTTP server is needed, no review API.
const {chromium}=require(process.env.METRO_PLAYWRIGHT_DIR || 'playwright');
const {spawn}=require('node:child_process');
const path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const cwd=path.resolve(__dirname,'..');
 const comparisonKey=JSON.stringify(['4','Châtelet','Cité']);
 const trips=JSON.parse(require('node:fs').readFileSync(path.join(cwd,'demo/comparisons.json'),'utf8')).trips;
 const expected=trips.filter(t=>JSON.stringify([t.metrics.line,t.metrics.origin_station,t.metrics.destination_station])===comparisonKey).length;
 assert(expected>=2);
 const server=spawn(process.env.METRO_PYTHON || 'python',['-m','http.server','8786','--bind','127.0.0.1','--directory',cwd]);
 let browser;
 try{
  browser=await chromium.launch({headless:true,executablePath:process.env.METRO_BROWSER_EXE});
  for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],failures=[];
   page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=400)failures.push(r.url());});
   await page.goto('http://127.0.0.1:8786/demo/');await page.locator('#departure-window').filter({hasText:'0.5–30.5'}).waitFor();
   await page.locator('img').evaluate(img=>img.decode());
   await page.getByRole('link',{name:'Compare interstation runs',exact:true}).click();
   const section=page.locator('#run-comparisons'),select=section.getByLabel('Interstation to compare');await select.waitFor();
   await select.selectOption(comparisonKey);
   const chart=section.locator('svg'),curves=chart.locator('polyline[data-recording]');assert.equal(await curves.count(),expected);
   assert.equal(await section.locator('.comparison-legend-row a').count(),0); // No missing individual-run anchors on Pages.
   assert((await section.locator('.comparison-legend').textContent()).includes('Montparnasse'));
   await section.getByRole('button',{name:'First 30 s',exact:true}).click();assert.equal(Number(await chart.getAttribute('data-view-end')),30);
   const toggle=section.getByRole('checkbox').first();await toggle.uncheck();assert.equal(await curves.count(),expected-1);await toggle.check();
   await section.getByRole('button',{name:'Zoom in',exact:true}).click();assert.equal(Number(await chart.getAttribute('data-view-end'))-Number(await chart.getAttribute('data-view-start')),15);
   await section.getByRole('button',{name:'Full runs',exact:true}).click();assert(Number(await chart.getAttribute('data-view-end'))>30);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   const response=await page.request.get('http://127.0.0.1:8786/demo/global_statistics.csv');assert(response.ok());
   if(width===1440 && process.env.METRO_PAGES_SCREENSHOT)await section.screenshot({path:process.env.METRO_PAGES_SCREENSHOT});
   if(width===1440){
    await page.evaluate(async()=>{
     const data=await (await fetch('comparisons.json')).json();
     window.renderRunComparisons(data.trips); // Report mode keeps its existing individual-run links.
    });
    await select.selectOption(comparisonKey);
    assert.equal(await section.locator('.comparison-legend-row a').count(),expected);
   }
   assert.deepEqual(errors,[]);assert.deepEqual(failures,[]);await page.close();
  }
  console.log('PASS: static Pages demo on desktop/mobile, Châtelet–Cité curves, zoom, visibility controls, boxplots and CSV, no broken links/assets or JavaScript errors.');
 }finally{if(browser)await browser.close();if(server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}}
})().catch(error=>{console.error(error);process.exitCode=1;});
