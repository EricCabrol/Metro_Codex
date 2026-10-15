// Optional integration check: supply METRO_PYTHON, METRO_PLAYWRIGHT_DIR and METRO_BROWSER_EXE as needed.
const {chromium}=require(process.env.METRO_PLAYWRIGHT_DIR || 'playwright');
const {spawn,spawnSync}=require('node:child_process');const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'metro-archive-browser-'));
 const cwd=path.resolve(__dirname,'..'), python=process.env.METRO_PYTHON || 'python',output=path.join(temp,'results'),data=path.join(temp,'data'),reviews=path.join(temp,'reviews');
 const env={...process.env};
 let result=spawnSync(python,['-c',`from pathlib import Path
import numpy as np,pandas as pd
p=Path(${JSON.stringify(data)})/'fixture';p.mkdir(parents=True)
t=np.r_[np.arange(0,10,.02),np.arange(20,40,.02)]
pd.DataFrame({'seconds_elapsed':t,'x':0,'y':np.sin(t),'z':0}).to_csv(p/'Accelerometer.csv',index=False)
`],{cwd,env,encoding:'utf8'});assert.equal(result.status,0,result.stderr);
 result=spawnSync(python,['-m','metro_braking','--data',data,'--output',output],{cwd,env,encoding:'utf8'});assert.equal(result.status,0,result.stderr);
 const original=fs.readFileSync(path.join(data,'fixture','Accelerometer.csv'));
 const server=spawn(python,['-m','metro_braking','--serve','--output',output,'--review-dir',reviews,'--port','8776'],{cwd,env});
 let logs='';server.stdout.on('data',x=>logs+=x);server.stderr.on('data',x=>logs+=x);let browser;
 try{
  for(let i=0;i<100&&!logs.includes('Review stops at');i++)await new Promise(r=>setTimeout(r,100));assert(logs.includes('Review stops at'),logs);
  browser=await chromium.launch({headless:true,executablePath:process.env.METRO_BROWSER_EXE});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8776/report.html');await page.locator('#review-save-status').filter({hasText:'Disk save connected'}).waitFor();
  assert.equal(await page.locator('.continuity-warning').count(),1);
  assert((await page.locator('.continuity-warning').textContent()).includes('10.0 s'));
  const invalid=await page.request.post('http://127.0.0.1:8776/api/recordings',{data:{recording:'../fixture',action:'archive'}});assert.equal(invalid.status(),400);
  page.once('dialog',d=>d.accept());
  await page.getByRole('button',{name:'Archive recording',exact:true}).click();
  await page.getByRole('button',{name:'Restore recording',exact:true}).waitFor({timeout:60000});
  assert.equal(await page.locator('.stop-review').count(),0);assert(!fs.existsSync(path.join(data,'fixture')));
  await page.getByRole('button',{name:'Restore recording',exact:true}).click();
  await page.getByRole('button',{name:'Archive recording',exact:true}).waitFor({timeout:60000});
  assert.equal(await page.locator('.stop-review').count(),1);assert.deepEqual(fs.readFileSync(path.join(data,'fixture','Accelerometer.csv')),original);
  assert.equal(await page.getByRole('button',{name:'Restore recording',exact:true}).count(),0);
  assert.deepEqual(errors,[]);
  console.log('PASS: browser gap warning, invalid-path rejection, archive to empty report, restore, source bytes unchanged, no JavaScript errors');
 }finally{if(browser)await browser.close();server.kill();await new Promise(r=>server.once('exit',r));fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
