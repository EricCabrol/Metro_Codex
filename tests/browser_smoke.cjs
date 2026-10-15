// Optional browser integration check; Python and Playwright paths are supplied
// by the local developer environment rather than added as runtime dependencies.
const {chromium} = require(process.env.METRO_PLAYWRIGHT_DIR || 'playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');

(async () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'metro-browser-'));
  const output = path.join(temporary, 'results');
  fs.mkdirSync(output);
  for (const file of fs.readdirSync('results')) {
    if (file.startsWith('report') || file.startsWith('signal_') || ['inventory.csv','orientation_checks.csv','comparison.png','trip_charts.js','trip_acceleration.png','trip_deceleration.png','trip_acceleration.csv'].includes(file))
      fs.copyFileSync(path.join('results',file),path.join(output,file));
  }
  fs.cpSync('results/plots', path.join(output,'plots'), {recursive:true});
  const server = spawn(process.env.METRO_TEST_PYTHON || 'python', ['-m','metro_braking','--serve','--output',output,'--review-dir',path.join(temporary,'reviews'),'--port','8766'], {windowsHide:true,stdio:['ignore','pipe','pipe']});
  let logs=''; server.stdout.on('data', chunk=>logs+=chunk); server.stderr.on('data',chunk=>logs+=chunk);
  let browser;
  try {
    for(let i=0;i<100;i++) {if(logs.includes('Review stops at'))break; if(server.exitCode!==null)throw Error(logs); await new Promise(r=>setTimeout(r,100));}
    assert(logs.includes('Review stops at'),logs);
    browser=await chromium.launch({headless:true,executablePath:process.env.METRO_BROWSER_EXE});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:8766/report.html');
    await page.locator('#review-save-status').filter({hasText:'Disk save connected'}).waitFor();
    assert.equal(await page.locator('.stop-review').count(),11);
    const reportData=JSON.parse(await page.locator('#review-data').textContent());
    const initialCanvas=page.locator('.stop-review').first().locator('canvas');
    assert.equal(Number(await initialCanvas.getAttribute('data-view-start')),reportData.recordings[0].start_s);
    assert.equal(Number(await initialCanvas.getAttribute('data-view-end')),reportData.recordings[0].end_s);
    const card=page.locator('.stop-review').nth(2);
    const rows=card.locator('tr[data-candidate-id]');
    assert(await rows.count()>=3);
    await rows.nth(0).getByRole('button',{name:'Accept',exact:true}).click();
    await page.locator('#review-save-status').filter({hasText:'Saved to reviews'}).waitFor();
    await rows.nth(1).getByRole('button',{name:'Discard',exact:true}).click();
    await page.locator('#review-save-status').filter({hasText:'Saved to reviews'}).waitFor();
    await page.reload();
    await page.locator('#review-save-status').filter({hasText:'Disk save connected'}).waitFor();
    assert.equal(await rows.nth(0).getAttribute('data-decision'),'accepted');
    assert.equal(await rows.nth(1).getAttribute('data-decision'),'discarded');
    await rows.nth(2).getByRole('button',{name:'Accept',exact:true}).click();
    await page.locator('#review-save-status').filter({hasText:'Saved to reviews'}).waitFor();
    const time=rows.nth(0).getByLabel('Arrival time');
    const adjusted=Number(await time.inputValue())-0.5;
    await time.fill(String(adjusted));await time.press('Tab');
    await page.locator('#review-save-status').filter({hasText:'Saved to reviews'}).waitFor();
    const restart=rows.nth(0).getByLabel('Restart time',{exact:true});
    const adjustedRestart=Number(await restart.inputValue())+0.5;
    await restart.fill(String(adjustedRestart));await restart.press('Tab');
    await page.locator('#review-save-status').filter({hasText:'Saved to reviews'}).waitFor();
    await page.getByRole('button',{name:'Export sections from accepted stops'}).click();
    await page.locator('#review-save-status').filter({hasText:'Exported 1 sections'}).waitFor();
    assert(fs.existsSync(path.join(output,'reviewed_sections.csv')));
    assert.equal(await page.locator('.trip-signal').count(),1);
    const trip=page.locator('.trip-signal').first();
    await trip.locator('summary').click();
    const chart=trip.locator('.trip-chart');
    assert((await chart.textContent()).includes('Seconds since restart time'));
    assert.equal(await trip.getByRole('button',{name:'Before arrival (20 s)',exact:true}).count(),1);
    assert.equal(await trip.locator('.trip-arrival-line').count(),1);
    assert(await rows.nth(0).getByLabel('Restart time',{exact:true}).count()===1);
    assert(await chart.locator('[data-kind="acceleration"]').count()===1);
    const before=Number(await chart.getAttribute('data-view-end'));
    await trip.getByRole('button',{name:'Zoom in',exact:true}).click();
    assert(Number(await chart.getAttribute('data-view-end'))-Number(await chart.getAttribute('data-view-start'))<before);
    await trip.getByRole('button',{name:'Full interstation run',exact:true}).click();
    const saved=JSON.parse(fs.readFileSync(path.join(temporary,'reviews','stops.json'),'utf8'));
    assert.equal(saved.decisions[await rows.nth(0).getAttribute('data-candidate-id')].time_s,adjusted);
    assert.equal(saved.decisions[await rows.nth(0).getAttribute('data-candidate-id')].restart_s,adjustedRestart);
    const csvDownload=page.waitForEvent('download');
    await page.getByRole('button',{name:'Download reviewed stops CSV'}).click();
    assert.equal((await csvDownload).suggestedFilename(),'stops.csv');
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({path:'results/review_ui.png',fullPage:false});
    assert.deepEqual(errors,[]);
    await browser.close();browser=null;
    // Static file mode has the same controls and browser persistence.
    browser=await chromium.launch({headless:true,executablePath:process.env.METRO_BROWSER_EXE});
    const offline=await browser.newPage();
    await offline.goto('file:///'+path.join(output,'report.html').replaceAll('\\','/'));
    const offlineRow=offline.locator('tr[data-candidate-id]').first();
    await offlineRow.getByRole('button',{name:'Accept',exact:true}).click();
    await offline.reload();
    assert.equal(await offlineRow.getAttribute('data-decision'),'accepted');
    assert(await offline.locator('#export-sections').isDisabled());
    console.log('Browser checks passed: accept/discard, reload persistence, arrival edit, section export, CSV download and offline mode.');
  } finally {
    if(browser)await browser.close();
    server.kill();await new Promise(r=>server.exitCode!==null?r():server.once('exit',r));
    // The only recursive cleanup target is the exact directory created above.
    assert(path.resolve(temporary).startsWith(path.resolve(os.tmpdir())+path.sep));
    fs.rmSync(temporary,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
