// Regression for large history: no eager editor DOM, release clean forms, retain edits.
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.BROWSER_EXECUTABLE_PATH?{executablePath:process.env.BROWSER_EXECUTABLE_PATH}:{})});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:18081/login');await page.getByRole('button',{name:'Войти как первый пользователь'}).click();await page.waitForURL('**/goals');
  const marker=await page.evaluate(()=>window.historyDocumentMarker=crypto.randomUUID());
  let editors=0,deletePosts=0;page.on('request',request=>{if(/\/activity\/\d+\/editor/.test(request.url()))editors++;if(/\/activity\/\d+\/delete/.test(request.url())&&request.method()==='POST')deletePosts++});
  await page.locator('header nav').getByRole('link',{name:'Редактирование',exact:true}).click();await page.waitForURL(url=>url.pathname==='/editing');
  if(await page.locator('.record-editor').count()!==50||await page.locator('.record-form').count()||editors)throw Error('History eagerly loaded editor forms');
  const initialNodes=await page.locator('main *').count();if(initialNodes>1000)throw Error('History DOM is too large: '+initialNodes);
  const first=page.locator('.record-editor').first(),summary=first.locator('summary'),form=first.locator('.record-form');
  await summary.click();await form.waitFor();
  if(editors!==1||await page.locator('.record-form').count()!==1)throw Error('Opening one record loaded multiple editors');
  const score=form.locator('select[name^="score_"]').first();
  if(await score.inputValue()!=='22'||await score.locator('option[value="22"]').count()!==1)throw Error('Historical score maximum lost');
  if(!await form.locator('[name="secondary"]').isVisible())throw Error('Exam editor not initialized');
  await summary.click();await page.waitForFunction(()=>!document.querySelector('.record-form'));
  if(await page.locator('main *').count()>initialNodes+5)throw Error('Closing clean editor did not free its DOM');
  await summary.click();await form.waitFor();if(editors!==2)throw Error('Released editor was not reloaded');
  await form.locator('[name="title"]').fill('История: несохранённая правка');
  await summary.click();await summary.click();
  if(await form.locator('[name="title"]').inputValue()!=='История: несохранённая правка'||editors!==2)throw Error('Closing editor lost unsaved input');
  await form.getByRole('button',{name:'Сохранить',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('.record-form')&&document.querySelector('.record-editor summary').textContent.includes('История: несохранённая правка'));
  await summary.click();await form.waitFor();
  page.once('dialog',dialog=>dialog.dismiss());
  await first.getByRole('button',{name:'Удалить запись',exact:true}).click();
  if(deletePosts||!await summary.textContent().then(text=>text.includes('История: несохранённая правка')))throw Error('Delete confirmation missing');
  await summary.click();
  // A failed individual editor fetch must not block the history or navigation.
  const second=page.locator('.record-editor').nth(1),url=await second.locator('[data-editor-url]').getAttribute('data-editor-url');
  await page.route('**'+url,route=>route.abort('failed'));
  await second.locator('summary').click();await second.getByRole('alert').waitFor();
  await page.unroute('**'+url);
  await second.getByRole('button',{name:'Повторить',exact:true}).click();await second.locator('.record-form').waitFor();
  await page.locator('header nav').getByRole('link',{name:'Цели',exact:true}).click();await page.waitForURL(url=>url.pathname==='/goals');
  if(await page.evaluate(()=>window.historyDocumentMarker)!==marker)throw Error('History actions reloaded document');
  if(errors.length)throw Error(errors.join('\n'));
  console.log(`Large-history regression OK: 50 records, ${initialNodes} initial DOM nodes, only expanded records allocate forms`);
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
