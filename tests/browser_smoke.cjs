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
  for(const width of [320,390,750,1280]){
   await page.setViewportSize({width,height:844});
   for(const route of ['/goals','/schedule','/analytics','/editing','/settings']){
    await page.goto('http://127.0.0.1:18081'+route);
    if(!await page.locator('header > nav').isVisible())throw Error('Sections not in header');
    if(width<=750){
     if(await page.locator('header > .theme-toggle').isVisible()||await page.locator('header > .profile-cards').isVisible())throw Error('Personal controls remain in mobile header');
     if(route==='/settings'&&(!await page.locator('#mobile-settings-controls #theme-toggle').isVisible()||!await page.locator('#mobile-settings-controls #student-switch').isVisible()))throw Error('Mobile settings controls missing');
    }else{
     if(!await page.locator('header > .theme-toggle').isVisible()||!await page.locator('header > .profile-cards').isVisible())throw Error('Desktop controls changed');
    }
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);
    if(overflow){
     await page.screenshot({path:'/tmp/study-mobile-overflow.png',fullPage:true});
     console.log(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(node=>node.getBoundingClientRect().right>innerWidth+2).map(node=>({tag:node.tagName,cls:node.className,width:node.getBoundingClientRect().width,right:node.getBoundingClientRect().right})).slice(0,20)));
     throw Error('Horizontal overflow: '+route+' '+width);
    }
    if(route==='/schedule'&&width===320)await page.screenshot({path:'/tmp/study-mobile.png',fullPage:true});
    if(['/goals','/schedule'].includes(route)){
     const bounds=await page.locator('.week-switch').boundingBox();
     if(bounds.x<0||bounds.x+bounds.width>width)throw Error('Week selector outside viewport');
     const cards=await page.locator('.card-board').boundingBox();
     if(cards.y<bounds.y+bounds.height)throw Error('Week selector overlaps cards');
     const label=await page.locator('.week-switch time').evaluate(node=>node.scrollWidth>node.clientWidth);
     if(label)throw Error('Week label overflows');
     if(width<=750){
      if((await page.locator('.week-switch time').boundingBox()).height!==30)throw Error('Week control is not compact');
      if(await page.locator('.week-switch a:visible').count())throw Error('Mobile week arrows remain visible');
      if(Math.abs(bounds.x+bounds.width/2-width/2)>1)throw Error('Week not centered');
      const background=await page.locator('.week-switch').evaluate(node=>getComputedStyle(node).backgroundColor);
      if(background!=='rgba(0, 0, 0, 0)')throw Error('Week control elements not separated');
     }
     if(route==='/schedule'&&width<=650&&Math.abs(cards.width-(width-32))>1)throw Error('Schedule does not fill available width');
    }
   }
  }
  await page.setViewportSize({width:390,height:844});
  await page.goto('http://127.0.0.1:18081/goals');
  // Mobile personal controls are available in settings and preserve plan context.
  await page.getByRole('link',{name:'Настройки',exact:true}).click();
  await page.waitForURL('**/settings?**');
  const selector=page.locator('#student-switch');
  await selector.selectOption('2');
  await page.waitForURL(url=>url.searchParams.get('student')==='2');
  if(await selector.inputValue()!=='2')throw Error('Pupil plan selection lost');
  await page.getByRole('link',{name:'Цели',exact:true}).click();
  await page.waitForURL('**/goals?**');
  const cdp=await context.newCDPSession(page);
  async function swipe(x1,y1,x2,y2){
   await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:x1,y:y1}]});
   for(let n=1;n<=6;n++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x1+(x2-x1)*n/6,y:y1+(y2-y1)*n/6}]});
   await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  }
  const currentWeek=await page.locator('.week-switch time').getAttribute('datetime');
  await swipe(270,300,90,305);
  await page.waitForURL(url=>url.searchParams.has('week'));
  const nextWeek=await page.locator('.week-switch time').getAttribute('datetime');
  if(nextWeek<=currentWeek||new URL(page.url()).searchParams.get('student')!=='2')throw Error('Swipe lost student or failed to advance');
  await swipe(90,300,270,305);
  await page.waitForFunction(week=>document.querySelector('.week-switch time').dateTime===week,currentWeek);
  const beforeVertical=page.url();
  await swipe(190,400,195,250);
  if(page.url()!==beforeVertical)throw Error('Vertical scroll switched week');
  await page.getByRole('link',{name:'Настройки',exact:true}).click();
  await page.waitForURL('**/settings?**');
  const oldTheme=await page.locator('html').getAttribute('data-theme');
  await page.locator('#mobile-settings-controls #theme-toggle').click();
  if(await page.locator('html').getAttribute('data-theme')===oldTheme)throw Error('Theme toggle failed in mobile settings');
  await selector.selectOption('1');
  await page.waitForURL(url=>url.searchParams.get('student')==='1');
  await page.screenshot({path:'/tmp/study-mobile-settings.png'});
  await page.getByRole('link',{name:'Расписание',exact:true}).click();
  await page.waitForURL('**/schedule?**');
  const scheduleWeek=await page.locator('.week-switch time').getAttribute('datetime');
  await swipe(270,240,90,245);
  await page.waitForFunction(week=>document.querySelector('.week-switch time').dateTime>week,scheduleWeek);
  await swipe(90,240,270,245);
  await page.waitForFunction(week=>document.querySelector('.week-switch time').dateTime===week,scheduleWeek);
  await page.screenshot({path:'/tmp/study-mobile-schedule.png'});
  await page.getByRole('link',{name:'Настройки',exact:true}).click();
  await page.waitForURL('**/settings?**');
  await page.setViewportSize({width:1280,height:844});
  await page.waitForFunction(()=>document.querySelector('header > #theme-toggle')&&document.querySelector('header > .profile-cards'));
  if(!await page.locator('header > nav').isVisible()||!await page.locator('header > #theme-toggle').isVisible())throw Error('Desktop resize did not restore controls');
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
   const key=(await caches.keys()).find(key=>key.startsWith('study-shell-'));const cache=await caches.open(key);return (await cache.keys()).map(r=>new URL(r.url).pathname);
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
