// Run only against tests/browser_server.py: the login deliberately uses debug mode.
const {chromium}=require('playwright');
(async()=>{
 const options={headless:true,args:['--no-sandbox']};
 if(process.env.BROWSER_EXECUTABLE_PATH)options.executablePath=process.env.BROWSER_EXECUTABLE_PATH;
 const browser=await chromium.launch(options);
 try {
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:18081/login');
  await page.getByRole('button',{name:'Войти как первый пользователь'}).click();
  await page.waitForURL('**/goals');
  await page.locator('#board-add').click();
  const goal=page.locator('dialog form');
  await goal.locator('[name="subject_id"]').selectOption({index:1});
  const groupId=await goal.locator('[name="group_id"] option:not([disabled])[data-subject]').first().getAttribute('value');
  await goal.locator('[name="group_id"]').selectOption(groupId);
  await goal.getByRole('button',{name:'Добавить',exact:true}).click();
  await page.waitForURL('**/goals?**');
  if(await page.locator('.goal-card').count()!==1)throw Error('Goal was not created');
  await page.goto('http://127.0.0.1:18081/schedule');
  await page.locator('#board-add').click();
  const slot=page.locator('dialog form');
  await slot.locator('[name="title"]').fill('Browser smoke slot');
  await slot.getByRole('button',{name:'Добавить',exact:true}).click();
  await page.waitForURL('**/schedule?**');
  if(await page.locator('.slot').count()!==1)throw Error('Slot was not created');
  for(const route of ['/analytics','/editing','/editing?tab=subjects','/settings']){
   const response=await page.goto('http://127.0.0.1:18081'+route);
   if(response.status()!==200)throw Error(route+' returned '+response.status());
  }
  const exported=await context.request.get('http://127.0.0.1:18081/account/export');
  if(exported.status()!==200||(await exported.json()).users[0].telegram_id!==100)throw Error('Export failed');
  await page.goto('http://127.0.0.1:18081/analytics');
  await page.locator('#analytics-period').selectOption('28');
  await page.waitForURL('**period=28');
  if(await page.locator('#analytics-period').inputValue()!=='28')throw Error('Period not retained');
  // Week navigation survives ordinary links but reload returns to today's week.
  await page.goto('http://127.0.0.1:18081/goals?week=2020-01-06');
  await page.getByRole('link',{name:'Следующая неделя',exact:true}).click();
  if(!page.url().includes('week=2020-01-13'))throw Error('Week navigation failed');
  await page.getByRole('link',{name:'Расписание',exact:true}).click();
  if(!page.url().includes('week=2020-01-13'))throw Error('Selected week lost between sections');
  await page.reload();
  await page.waitForURL(url=>!url.searchParams.has('week'));
  if(await page.locator('.week-switch time').getAttribute('datetime')==='2020-01-13')throw Error('Reload kept old week');
  for(const width of [320,390,1280]){
   await page.setViewportSize({width,height:844});
   for(const route of ['/goals','/schedule','/analytics','/editing','/settings']){
    await page.goto('http://127.0.0.1:18081'+route);
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);
    if(overflow)throw Error('Horizontal overflow: '+route+' '+width);
    if(route==='/schedule'&&width===320)await page.screenshot({path:'/tmp/study-mobile.png',fullPage:true});
    if(['/goals','/schedule'].includes(route)){
     const bounds=await page.locator('.week-switch').boundingBox();
     if(bounds.x<0||bounds.x+bounds.width>width)throw Error('Week selector outside viewport');
     const cards=await page.locator('.card-board').boundingBox();
     if(cards.y<bounds.y+bounds.height)throw Error('Week selector overlaps cards');
     const label=await page.locator('.week-switch time').evaluate(node=>node.scrollWidth>node.clientWidth);
     if(label)throw Error('Week label overflows');
    }
   }
  }
  await page.addInitScript(()=>{
   const original=window.matchMedia.bind(window);
   window.matchMedia=query=>query==='(display-mode: standalone)'?{matches:true}:original(query);
  });
  await page.goto('http://127.0.0.1:18081/goals?week=2020-01-06');
  await page.evaluate(()=>{
   Object.defineProperty(document,'hidden',{configurable:true,value:true});
   document.dispatchEvent(new Event('visibilitychange'));
   Object.defineProperty(document,'hidden',{configurable:true,value:false});
   document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.waitForURL(url=>!url.searchParams.has('week'));
  const cachedRequests=await page.evaluate(async()=>{
   const cache=await caches.open('study-shell-v1');return (await cache.keys()).map(r=>new URL(r.url).pathname);
  });
  if(cachedRequests.some(path=>!path.startsWith('/static/')))throw Error('Private page cached');
  // Installable metadata, root worker, offline navigation, and recovery.
  const manifest=await (await context.request.get('http://127.0.0.1:18081/static/manifest.webmanifest')).json();
  if(manifest.start_url!=='/goals'||manifest.display!=='standalone')throw Error('Invalid manifest');
  await page.evaluate(async()=>{await navigator.serviceWorker.ready;if(!navigator.serviceWorker.controller)await new Promise(resolve=>navigator.serviceWorker.addEventListener('controllerchange',resolve,{once:true}))});
  await context.setOffline(true);
  await page.goto('http://127.0.0.1:18081/goals');
  await page.getByRole('heading',{name:'Нет подключения',exact:true}).waitFor();
  await context.setOffline(false);
  await page.getByRole('link',{name:'Повторить',exact:true}).click();
  await page.locator('.week-switch').waitFor();
  await page.goto('http://127.0.0.1:18081/settings');
  await page.getByRole('button',{name:'Выйти',exact:true}).click();
  await page.waitForURL('**/login');
  if(errors.length)throw Error(errors.join('\n'));
  console.log('Browser desktop/mobile smoke OK');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
