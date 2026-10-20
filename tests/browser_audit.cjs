// Adversarial SPA lifecycles: pending responses, resumed forms, writes/history, retained DOM.
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.BROWSER_EXECUTABLE_PATH?{executablePath:process.env.BROWSER_EXECUTABLE_PATH}:{})});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:18081/login');await page.getByRole('button',{name:'Войти как первый пользователь'}).click();await page.waitForURL('**/goals');
  const marker=await page.evaluate(()=>window.auditMarker=crypto.randomUUID());
  const navigate=path=>page.evaluate(path=>window.StudyRouter.navigate(path,'section',{force:true}),path);
  await navigate('/settings');
  const profile=page.locator('form[action="/profile"]'),name=profile.locator('[name="name"]');
  await name.fill('Unsaved after resume');
  await page.evaluate(()=>{
   Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));
   Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.locator('#navigation-notice').waitFor();
  if(await name.inputValue()!=='Unsaved after resume')throw Error('Resume discarded unsaved input');
  // Deployment during an automatic refresh must not reload a dirty form either.
  await page.route('**/settings',async route=>{
   const response=await route.fetch(),data=await response.json();
   await route.fulfill({response,json:{...data,version:'new-deployment'}});
  });
  await page.evaluate(()=>window.StudyRouter.navigate('/settings','section',{replace:true,force:true,preserve:true}));
  if(await name.inputValue()!=='Unsaved after resume'||await page.evaluate(()=>window.auditMarker)!==marker)throw Error('Deployment discarded input');
  await page.unroute('**/settings');
  // A response body can finish parsing after cancellation. It must have no navigation side effects.
  await page.evaluate(()=>{
   const native=window.fetch;window.auditResponseStarted=false;
   window.fetch=async(...args)=>{
    const response=await native(...args);
    if(String(args[0]).includes('/analytics?period=84')){
     const data=await response.json();
     return {url:response.url,status:response.status,ok:response.ok,headers:response.headers,json:async()=>{
      window.auditResponseStarted=true;await new Promise(resolve=>window.auditRelease=resolve);
      window.auditResponseFinished=true;return {...data,version:'obsolete-version'};
     }};
    }
    return response;
   };
   window.StudyRouter.navigate('/analytics?period=84','section',{force:true});
  });
  await page.waitForFunction(()=>window.auditResponseStarted);
  await navigate('/schedule');
  await page.evaluate(()=>window.auditRelease());await page.waitForFunction(()=>window.auditResponseFinished);
  await page.waitForTimeout(150);
  if(new URL(page.url()).pathname!=='/schedule'||await page.evaluate(()=>window.auditMarker)!==marker)throw Error('Cancelled response navigated away');
  // Back during a write is deferred until confirmation, without replaying the POST.
  await navigate('/goals');await navigate('/settings');
  await name.fill('Saved during back');
  let release,posts=0;
  const held=new Promise(resolve=>release=resolve);
  await page.route('**/profile',async route=>{posts++;await held;await route.continue()});
  const started=page.waitForRequest(request=>new URL(request.url()).pathname==='/profile'&&request.method()==='POST');
  await profile.getByRole('button',{name:'Сохранить',exact:true}).click();await started;
  await page.goBack();await page.waitForURL('**/goals');
  await profile.evaluate(form=>form.requestSubmit());
  release();await page.locator('.planning-board').waitFor();await page.unroute('**/profile');
  if(posts!==1||new URL(page.url()).pathname!=='/goals')throw Error('Back/write race or duplicate POST');
  await navigate('/settings');if(await name.inputValue()!=='Saved during back')throw Error('Write was lost during back');
  // Failed writes restore the matching URL and preserve input even after Back.
  await navigate('/goals');await navigate('/settings');await name.fill('Failed save retained');
  let fail;const heldFailure=new Promise(resolve=>fail=resolve);
  await page.route('**/profile',async route=>{await heldFailure;await route.abort('failed')});
  const failing=page.waitForRequest(request=>new URL(request.url()).pathname==='/profile'&&request.method()==='POST');
  await profile.getByRole('button',{name:'Сохранить',exact:true}).click();await failing;
  await page.goBack();await page.waitForURL('**/goals');fail();
  await page.locator('#navigation-notice').waitFor();await page.waitForURL('**/settings');
  if(await name.inputValue()!=='Failed save retained')throw Error('Failed write/back discarded input');
  await page.unroute('**/profile');
  // Old listeners and detached views must be collectable across repeated transitions.
  await navigate('/goals');await page.waitForLoadState('networkidle');
  const cdp=await context.newCDPSession(page);
  await cdp.send('HeapProfiler.collectGarbage');const before=await cdp.send('Memory.getDOMCounters');
  for(let n=0;n<40;n++)await navigate(n%2?'/goals':'/settings');
  await page.waitForLoadState('networkidle');await cdp.send('HeapProfiler.collectGarbage');
  const after=await cdp.send('Memory.getDOMCounters');
  if(after.documents>before.documents+2||after.nodes>before.nodes+1000||after.jsEventListeners>before.jsEventListeners+100)throw Error('Views retained after GC: '+JSON.stringify({before,after}));
  if(await page.evaluate(()=>window.auditMarker)!==marker||errors.length)throw Error('Document reloaded or page errors: '+errors.join('; '));
  console.log('SPA audit OK: dirty resume, stale response, Back during writes, no duplicate POST; DOM after 40 transitions:',JSON.stringify({before,after}));
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
