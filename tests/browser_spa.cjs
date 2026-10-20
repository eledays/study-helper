// SPA regressions: one document, forms, history, races, and failure recovery.
const {chromium}=require('playwright');
(async()=>{
 const options={headless:true,args:['--no-sandbox']};
 if(process.env.BROWSER_EXECUTABLE_PATH)options.executablePath=process.env.BROWSER_EXECUTABLE_PATH;
 const browser=await chromium.launch(options);
 try{
  const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:18081/login');
  await page.getByRole('button',{name:'Войти как первый пользователь'}).click();
  await page.waitForURL('**/goals');
  await page.waitForFunction(()=>window.StudyRouter?.ready);
  const marker=await page.evaluate(()=>window.documentMarker=crypto.randomUUID());
  const documentRequests=[];
  page.on('request',request=>{if(request.isNavigationRequest())documentRequests.push(request.url())});
  async function sameDocument(){if(await page.evaluate(()=>window.documentMarker)!==marker)throw Error('Document reloaded')}
  async function section(name,path){
   await page.locator('header nav').getByRole('link',{name,exact:true}).click();
   await page.waitForURL(url=>url.pathname===path);
   await page.waitForFunction(path=>new URL(document.querySelector('header [aria-current="page"]').href).pathname===path,path);
   await sameDocument();
  }
  // Wait for idle prefetch, then deliberately slow the foreground request.
  await page.waitForLoadState('networkidle');
  const settingsURL=await page.locator('header nav').getByRole('link',{name:'Настройки',exact:true}).getAttribute('href');
  await page.evaluate(url=>window.StudyRouter.prefetch(url),settingsURL);
  await page.route('**/settings?**',async route=>{
   if(route.request().headers()['x-study-navigation']&&!route.request().headers()['x-study-prefetch'])await new Promise(resolve=>setTimeout(resolve,600));
   await route.continue().catch(()=>{});
  });
  const cachedMs=await page.evaluate(url=>new Promise(resolve=>{
   const start=performance.now();document.addEventListener('study:view',()=>resolve(performance.now()-start),{once:true});navigateWithMotion(url);
  }),settingsURL);
  if(cachedMs>250)throw Error('Prefetched view did not render immediately: '+cachedMs);
  await sameDocument();
  await page.unroute('**/settings?**');
  const selection=page.locator('#student-switch');
  await selection.selectOption('2');
  await page.waitForURL(url=>url.searchParams.get('student')==='2');
  await section('Расписание','/schedule');
  if(new URL(page.url()).searchParams.get('student')!=='2')throw Error('Student context lost');
  await section('Настройки','/settings');
  await selection.selectOption('1');
  await page.waitForURL(url=>url.searchParams.get('student')==='1');
  await section('Цели','/goals');
  const count=await page.locator('.goal-card').count();
  await page.locator('#board-add').click();
  const goal=page.locator('#board-create form');
  await goal.locator('[name="subject_id"]').selectOption({index:1});
  const group=await goal.locator('[name="group_id"] option:not([disabled])[data-subject]').first().getAttribute('value');
  await goal.locator('[name="group_id"]').selectOption(group);
  await goal.getByRole('button',{name:'Добавить',exact:true}).click();
  await page.waitForFunction(count=>document.querySelectorAll('.goal-card').length===count+1,count);
  await sameDocument();
  await section('Расписание','/schedule');await section('Цели','/goals');
  if(await page.locator('.goal-card').count()!==count+1)throw Error('Mutation cache was not invalidated');
  const goalsURL=page.url();
  await section('Аналитика','/analytics');const analyticsURL=page.url();
  await section('Настройки','/settings');
  await page.goBack();await page.waitForURL(analyticsURL);await page.locator('#analytics-period').waitFor();await sameDocument();
  await page.goBack();await page.waitForURL(goalsURL);await page.locator('.goal-card').first().waitFor();await sameDocument();
  await page.goForward();await page.waitForURL(analyticsURL);await page.locator('#analytics-period').waitFor();
  await page.locator('#analytics-period').selectOption('28');
  await page.waitForURL(url=>url.searchParams.get('period')==='28');await sameDocument();
  await section('Редактирование','/editing');
  await page.getByRole('link',{name:'Предметы и задания',exact:true}).click();
  await page.waitForURL(url=>url.searchParams.get('tab')==='subjects');await sameDocument();
  // A late response must never overwrite the latest selection.
  await page.evaluate(()=>window.StudyRouter.clearCache());
  const scheduleURL=await page.locator('header nav').getByRole('link',{name:'Расписание',exact:true}).getAttribute('href');
  await page.route('**/analytics?**',async route=>{await new Promise(resolve=>setTimeout(resolve,300));await route.continue().catch(()=>{})});
  await page.evaluate(url=>{navigateWithMotion('/analytics?period=84');navigateWithMotion(url)},scheduleURL);
  await page.waitForURL(url=>url.pathname==='/schedule');
  await page.waitForLoadState('networkidle');
  if(new URL(page.url()).pathname!=='/schedule')throw Error('Stale response won navigation race');
  await page.unroute('**/analytics?**');
  // An offline read leaves the current view intact and can be retried in place.
  await page.evaluate(()=>window.StudyRouter.clearCache());await context.setOffline(true);
  await page.locator('header nav').getByRole('link',{name:'Настройки',exact:true}).click();
  await page.locator('#navigation-notice').waitFor();
  if(new URL(page.url()).pathname!=='/schedule')throw Error('Offline navigation destroyed current view');
  await sameDocument();await context.setOffline(false);
  await page.locator('#navigation-notice').getByRole('button',{name:'Повторить'}).click();
  await page.waitForURL(url=>url.pathname==='/settings');await sameDocument();
  // Restore scroll when returning through the browser's real history.
  const settingsHistoryURL=page.url();
  const savedY=await page.evaluate(()=>{scrollTo(0,350);return scrollY});
  await page.evaluate(()=>navigateWithMotion('/goals'));
  await page.waitForURL('**/goals');
  await page.goBack();await page.waitForURL(settingsHistoryURL);
  await page.waitForFunction(saved=>Math.abs(scrollY-saved)<2,savedY);
  await sameDocument();
  // Failed writes are not automatically replayed and retain user input.
  let posts=0;
  await page.route('**/profile',route=>{posts++;return route.abort('failed')});
  const profile=page.locator('form[action="/profile"]');
  await profile.locator('[name="name"]').fill('Unsaved SPA name');
  await profile.getByRole('button',{name:'Сохранить',exact:true}).click();
  await page.locator('#navigation-notice').waitFor();
  if(posts!==1||await profile.locator('[name="name"]').inputValue()!=='Unsaved SPA name')throw Error('Failed write was replayed or input lost');
  await sameDocument();await page.unroute('**/profile');
  if(documentRequests.length)throw Error('Internal app navigation requested documents: '+documentRequests.join(', '));
  // An expired login intentionally exits the SPA; login scripts load normally.
  await context.clearCookies();await page.evaluate(()=>window.StudyRouter.clearCache());
  await page.locator('header nav').getByRole('link',{name:'Цели',exact:true}).click();
  await page.waitForURL('**/login');
  if(await page.locator('.goal-card').count())throw Error('Private view survived logout');
  if(errors.length)throw Error(errors.join('\n'));
  console.log(`SPA history/forms/races/offline/session OK; prefetched view ${Math.round(cachedMs)} ms; no internal document requests`);
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
