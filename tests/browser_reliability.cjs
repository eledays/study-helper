// Regressions from the security/stability audit. Disposable browser_server.py only.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.BROWSER_EXECUTABLE_PATH?{executablePath:process.env.BROWSER_EXECUTABLE_PATH}:{})});
 try{
  const context=await browser.newContext({viewport:{width:1280,height:900},reducedMotion:'reduce',serviceWorkers:'block'}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\//,route=>route.abort());
  await page.goto('http://127.0.0.1:18081/login');await page.getByRole('button',{name:'Войти как первый пользователь'}).click();await page.waitForURL('**/goals');
  const marker=await page.evaluate(()=>window.reliabilityMarker=crypto.randomUUID());
  const navigate=path=>page.evaluate(path=>StudyRouter.navigate(path,'section',{force:true}),path);
  await navigate('/editing?tab=plan&student=1');await page.waitForLoadState('networkidle');
  await page.evaluate(()=>StudyRouter.clearCache());
  await page.route('**/editing?tab=plan&student=2',route=>route.abort('failed'));
  await page.locator('#student-switch').selectOption('2');await page.locator('#navigation-notice').waitFor();
  assert.equal(await page.locator('#student-switch').inputValue(),'1');
  assert.equal(new URL(page.url()).searchParams.get('student'),'1');
  assert.equal(await page.locator('main form [name="student"]').first().inputValue(),'1');
  await page.unroute('**/editing?tab=plan&student=2');
  await page.locator('#navigation-notice button').click();await page.waitForURL(url=>url.searchParams.get('student')==='2');
  assert.equal(await page.locator('#student-switch').inputValue(),'2');
  await navigate('/settings?student=1');
  const profile=page.locator('form[action="/profile"]');
  await profile.locator('[name="name"]').fill('Retained after validation');await profile.locator('[name="timezone"]').fill('Europe/Mosco');
  await profile.getByRole('button',{name:'Сохранить',exact:true}).click();await profile.getByRole('alert').waitFor();
  assert.equal(await profile.locator('[name="name"]').inputValue(),'Retained after validation');
  assert.equal(await profile.locator('[name="timezone"]').inputValue(),'Europe/Mosco');
  assert.equal(await page.evaluate(()=>window.reliabilityMarker),marker);
  // Corrected input can be saved without reloading or replaying the failed write.
  await profile.locator('[name="timezone"]').fill('Europe/Moscow');
  await profile.getByRole('button',{name:'Сохранить',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#navigation-notice')&&!document.querySelector('form[data-submitting]'));
  await navigate('/settings?student=1');assert.equal(await profile.locator('[name="name"]').inputValue(),'Retained after validation');
  // A validation failure inside a modal remains visible and keeps the modal/input.
  await navigate('/schedule?student=1');await page.locator('#board-add').click();
  const slot=page.locator('#board-create form');await slot.locator('[name="title"]').fill('Invalid time retained');
  await slot.locator('[name="start"]').fill('20:00');await slot.locator('[name="end"]').fill('19:00');
  await slot.getByRole('button',{name:'Добавить',exact:true}).click();await slot.getByRole('alert').waitFor();
  assert.equal(await page.locator('#board-create').evaluate(node=>node.open),true);
  assert.equal(await slot.locator('[name="title"]').inputValue(),'Invalid time retained');
  assert.equal(await slot.getByRole('alert').isVisible(),true);
  await page.keyboard.press('Escape');
  await navigate('/analytics?period=56');await page.waitForLoadState('networkidle');await page.evaluate(()=>StudyRouter.clearCache());
  await page.route('**/analytics?period=28',route=>route.abort('failed'));
  await page.locator('#analytics-period').selectOption('28');await page.locator('#navigation-notice').waitFor();
  assert.equal(await page.locator('#analytics-period').inputValue(),'56');await page.unroute('**/analytics?period=28');
  await page.setViewportSize({width:390,height:844});await navigate('/goals');
  assert.equal(await page.locator('header nav .section-icon svg').count(),5);
  assert.equal(await page.locator('#board-add svg').count(),1);
  assert.equal(await page.locator('header nav').evaluate(node=>node.scrollWidth>node.clientWidth),false);
  await page.screenshot({path:'/tmp/study-local-icons.png'});
  // Every standalone section refreshes on resume, while preserving dirty input.
  await page.addInitScript(()=>{const original=window.matchMedia.bind(window);window.matchMedia=q=>q==='(display-mode: standalone)'?{matches:true}:original(q)});
  const resume=()=>page.evaluate(()=>{
   Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));
   Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.goto('http://127.0.0.1:18081/settings');await profile.locator('[name="name"]').fill('Unsaved in PWA');
  await resume();await page.locator('#navigation-notice').waitFor();
  assert.equal(await profile.locator('[name="name"]').inputValue(),'Unsaved in PWA');
  await navigate('/analytics');await page.waitForLoadState('networkidle');
  const refreshed=page.waitForResponse(response=>new URL(response.url()).pathname==='/analytics'&&response.request().headers()['x-study-navigation']==='1');
  await resume();assert.equal((await refreshed).status(),200);
  await page.waitForLoadState('networkidle');await context.clearCookies();await resume();await page.waitForURL('**/login');
  assert.equal(await page.locator('#analytics-data').count(),0);
  assert.deepEqual(errors,[]);
  console.log('Reliability regressions OK: failed student/period switches, validation/profile/modal, local icons, standalone resume/session');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
