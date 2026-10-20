// One document, server-authorized views, and an in-memory cache scoped to this login.
function resetPlanningWeek(refresh = false) {
 const url = new URL(location.href);
 if (!['/goals','/schedule','/plan','/editing'].includes(url.pathname)) return;
 const selected = url.searchParams.has('week');
 if (!selected && !refresh) return;
 url.searchParams.delete('week'); url.searchParams.delete('edit');
 if (window.StudyRouter?.ready) window.StudyRouter.navigate(url.href,'section',{replace:true,force:true});
 else if (selected) location.replace(url.href);
 else location.reload();
}
if (performance.getEntriesByType('navigation')[0]?.type === 'reload') resetPlanningWeek();
function navigateWithMotion(destination, direction = 'section') {
 return window.StudyRouter?.ready ? window.StudyRouter.navigate(destination,direction) : location.assign(destination);
}

window.StudyRouter = (() => {
 const routes = new Set(['/goals','/schedule','/plan','/analytics','/editing','/settings']);
 const cache = new Map(), prefetches = new Map();
 const MAX_ENTRIES = 10, MAX_BYTES = 2000000, TTL = 10000;
 let ready = false, sequence = 0, epoch = 0, activeRequest, idleTask, loadingTimer;
 let user, version, currentHTML = '', renderedURL = '', mutation = false;
 const dirtyViews = new WeakSet();
 const appURL = value => {const url=new URL(value,location.href);return url.origin===location.origin&&routes.has(url.pathname)?url:null};
 const canonical = value => {const url=new URL(value,location.href);url.hash='';return url.href};
 const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
 function clearCache() {
  epoch++; cache.clear(); prefetches.forEach(controller=>controller.abort()); prefetches.clear();
  clearTimeout(idleTask);
 }
 function putCache(url,data) {
  if (data.html.includes('role="status"') || data.html.length > MAX_BYTES/2) return;
  cache.delete(url);cache.set(url,{data,time:Date.now()});
  let bytes=0;for(const entry of cache.values())bytes+=entry.data.html.length*2;
  while(cache.size>MAX_ENTRIES||bytes>MAX_BYTES){const key=cache.keys().next().value;bytes-=cache.get(key).data.html.length*2;cache.delete(key)}
 }
 function getCache(url) {
  const entry=cache.get(url);
  if(!entry||Date.now()-entry.time>TTL){cache.delete(url);return null}
  cache.delete(url);cache.set(url,entry);return entry.data;
 }
 function busy(value) {
  clearTimeout(loadingTimer);
  if(value) loadingTimer=setTimeout(()=>document.documentElement.dataset.loading='true',160);
  else {delete document.documentElement.dataset.loading;document.querySelector('main')?.removeAttribute('aria-busy')}
  if(value)document.querySelector('main')?.setAttribute('aria-busy','true');
 }
 function announce(text) {
  let status=document.getElementById('navigation-status');
  if(!status){status=document.createElement('div');status.id='navigation-status';status.className='sr-only';status.setAttribute('role','status');status.setAttribute('aria-live','polite');document.body.append(status)}
  status.textContent=text;
 }
 function notice(text,retry) {
  document.getElementById('navigation-notice')?.remove();
  const banner=document.createElement('div');banner.id='navigation-notice';banner.className='notice navigation-notice';banner.setAttribute('role','alert');
  const message=document.createElement('span');message.textContent=text;banner.append(message);
  if(retry){const button=document.createElement('button');button.type='button';button.className='quiet';button.textContent='Повторить';button.addEventListener('click',retry);banner.append(button)}
  document.querySelector('main')?.prepend(banner);
 }
 function hardNavigate(url) {clearCache();location.assign(url)}
 async function readView(response) {
  const destination=appURL(response.url);
  if(!destination){hardNavigate(response.url);return null}
  if(response.status===401||response.status===403){hardNavigate(response.url);return null}
  if(!response.ok)throw new Error('view-unavailable');
  if(!response.headers.get('Content-Type')?.includes('application/json'))throw new Error('unexpected-view');
  const data=await response.json();
  if(!data||typeof data.html!=='string'||!appURL(data.url)||String(data.user_id)!==user||data.version!==version){hardNavigate(response.url);return null}
  return data;
 }
 async function fetchView(url,signal,prefetch=false) {
  const response=await fetch(url,{signal,credentials:'same-origin',cache:'no-store',headers:{'X-Study-Navigation':'1',...(prefetch?{'X-Study-Prefetch':'1'}:{})}});
  // A background request must never navigate, reveal notices, or change accounts.
  if(prefetch){
   if(!response.ok||!appURL(response.url)||!response.headers.get('Content-Type')?.includes('application/json'))return null;
   const data=await response.json();
   return String(data.user_id)===user&&data.version===version&&typeof data.html==='string'&&appURL(data.url)?data:null;
  }
  return readView(response);
 }
 function snapshotScroll() {
  if(location.href===renderedURL)history.replaceState({...history.state,study:true,scroll:[scrollX,scrollY]},'',location.href);
 }
 function render(data,direction,{historyMode='push',scroll=[0,0],focus=true}={}) {
  const target=appURL(data.url);
  if(!target)throw new Error('invalid-route');
  const parsed=new DOMParser().parseFromString(data.html,'text/html');
  const nextMain=parsed.querySelector('main'),nextHeader=parsed.querySelector('header');
  if(!nextMain||!nextHeader||parsed.querySelector('script:not([type="application/json"])'))throw new Error('invalid-markup');
  window.disposeStudyView?.();
  document.querySelectorAll('dialog[open]').forEach(dialog=>dialog.close());
  document.documentElement.classList.remove('modal-open');
  document.documentElement.dataset.navigation=direction;
  if(historyMode==='push'){snapshotScroll();history.pushState({study:true,scroll},'',target.href)}
  else if(historyMode==='replace')history.replaceState({study:true,scroll},'',target.href);
  document.querySelector('body > header').replaceWith(document.importNode(nextHeader,true));
  document.querySelector('main').replaceWith(document.importNode(nextMain,true));
  currentHTML=data.html;renderedURL=target.href;
  window.initializeStudyView();
  const main=document.querySelector('main');main.tabIndex=-1;
  if(focus)main.focus({preventScroll:true});
  window.scrollTo(...scroll);
  announce(document.querySelector('header nav [aria-current="page"]')?.getAttribute('aria-label')||'Страница открыта');
  document.dispatchEvent(new CustomEvent('study:view',{detail:{url:target.href,direction}}));
 }
 async function prefetch(url) {
  const target=appURL(url);if(!ready||!target||mutation||document.hidden)return;
  const key=canonical(target.href);if(getCache(key)||prefetches.has(key)||key===canonical(renderedURL))return;
  if(prefetches.size>=2)return;
  const controller=new AbortController(),started=epoch;prefetches.set(key,controller);
  const timeout=setTimeout(()=>controller.abort(),7000);
  try{const data=await fetchView(key,controller.signal,true);if(data&&started===epoch&&!controller.signal.aborted)putCache(key,data)}catch(_){}finally{clearTimeout(timeout);if(prefetches.get(key)===controller)prefetches.delete(key)}
 }
 function schedulePrefetch() {
  clearTimeout(idleTask);
  if(navigator.connection?.saveData||navigator.connection?.effectiveType==='2g')return;
  const started=epoch;
  idleTask=setTimeout(async()=>{
   const urls=[...document.querySelectorAll('header nav a,.week-switch > a')].map(link=>link.href);
   // Two at a time: don't compete with charts, image loading, or SQLite writes.
   for(let index=0;index<urls.length;index+=2){if(started!==epoch||mutation||document.hidden)return;await Promise.all(urls.slice(index,index+2).map(prefetch))}
  },250);
 }
 async function navigate(destination,direction='section',options={}) {
  const url=appURL(destination);if(!url){hardNavigate(destination);return}
  if(!ready){location.assign(url.href);return}
  if(mutation){announce('Дождитесь сохранения');return}
  const key=canonical(url.href),requestID=++sequence;
  activeRequest?.abort();activeRequest=new AbortController();const controller=activeRequest;
  const timeout=setTimeout(()=>controller.abort('timeout'),15000);
  clearTimeout(idleTask);document.getElementById('navigation-notice')?.remove();
  const mode=options.pop?'none':options.replace?'replace':'push';
  const scroll=options.scroll||[0,0],cached=options.force?null:getCache(key);
  let committed=false;
  try{
   if(cached){render(cached,direction,{historyMode:mode,scroll});committed=true}
   busy(!cached);
   const data=await fetchView(key,controller.signal);
   if(!data||requestID!==sequence)return;
   putCache(canonical(new URL(data.url,location.href).href),data);
   if(!committed){render(data,direction,{historyMode:mode,scroll});committed=true}
   else if(currentHTML!==data.html){
    if(dirtyViews.has(document.querySelector('main'))||document.querySelector('dialog[open]'))notice('Данные обновились. Сохраните изменения перед обновлением экрана.',()=>navigate(location.href,'section',{replace:true,force:true}));
    else render(data,direction,{historyMode:'replace',scroll:[scrollX,scrollY],focus:false});
   }
   schedulePrefetch();
  }catch(error){
   if(requestID!==sequence||(error.name==='AbortError'&&controller.signal.reason!=='timeout'))return;
   if(options.pop&&!committed)history.replaceState({study:true,scroll:[scrollX,scrollY]},'',renderedURL);
   notice(committed?'Не удалось обновить данные. Показана ранее загруженная версия.':'Не удалось загрузить раздел. Проверьте подключение и повторите.',()=>navigate(key,direction,{...options,pop:false,force:true}));
  }finally{clearTimeout(timeout);if(requestID===sequence)busy(false)}
 }
 async function submit(form,submitter) {
  if(mutation||form.dataset.submitting)return;
  const method=(form.getAttribute('method')||'get').toLowerCase(),data=new FormData(form);
  if(submitter?.name)data.append(submitter.name,submitter.value);
  const url=new URL(form.getAttribute('action')||location.href,location.href);
  if(method==='get'){url.search=new URLSearchParams(data).toString();return navigate(url.href)}
  mutation=true;form.dataset.submitting='true';activeRequest?.abort();const requestID=++sequence;clearCache();busy(true);
  const buttons=[...form.querySelectorAll('button:not([type]),button[type="submit"],input[type="submit"]')].map(button=>({button,disabled:button.disabled}));
  buttons.forEach(({button})=>button.disabled=true);
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
  try{
   const response=await fetch(url.href,{method:'POST',signal:controller.signal,body:new URLSearchParams(data),credentials:'same-origin',cache:'no-store',headers:{'X-Study-Navigation':'1'}});
   const view=await readView(response);if(!view||requestID!==sequence)return;
   render(view,'section',{historyMode:'push'});putCache(canonical(new URL(view.url,location.href).href),view);
  }catch(_){notice('Не удалось подтвердить сохранение. Проверьте подключение и обновите страницу, прежде чем повторять отправку.');}
  finally{clearTimeout(timeout);mutation=false;delete form.dataset.submitting;buttons.forEach(({button,disabled})=>button.disabled=disabled);busy(false);schedulePrefetch()}
 }
 document.addEventListener('click',event=>{
  const link=event.target.closest('a[href]');
  if(!ready||!link||event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.target||link.hasAttribute('download')||link.dataset.native!==undefined)return;
  const url=appURL(link.href);if(!url||url.hash)return;
  event.preventDefault();
  const oldWeek=document.querySelector('.week-switch time')?.dateTime,nextWeek=url.searchParams.get('week');
  const direction=link.closest('header nav')||!oldWeek||!nextWeek||oldWeek===nextWeek?'section':nextWeek>oldWeek?'next':'previous';
  navigate(url.href,direction);
 });
 document.addEventListener('submit',event=>{
  const form=event.target,method=(form.getAttribute('method')||'get').toLowerCase(),url=new URL(form.getAttribute('action')||location.href,location.href);
  if(!ready||event.defaultPrevented||form.getAttribute('target')||form.dataset.native!==undefined||url.origin!==location.origin||!['get','post'].includes(method)||form.querySelector('input[type="file"]'))return;
  if(method==='get'&&!appURL(url.href))return;
  if(method==='post'&&['/logout','/account/delete','/auth/debug','/auth/telegram'].includes(url.pathname)){clearCache();return}
  event.preventDefault();submit(form,event.submitter);
 });
 document.addEventListener('input',event=>{const main=event.target.closest('main');if(main&&event.target.closest('form'))dirtyViews.add(main)});
 document.addEventListener('change',event=>{const main=event.target.closest('main');if(main&&event.target.closest('form'))dirtyViews.add(main)});
 document.addEventListener('pointerover',event=>{const link=event.target.closest('a[href]');if(link)prefetch(link.href)});
 document.addEventListener('focusin',event=>{const link=event.target.closest('a[href]');if(link)prefetch(link.href)});
 window.addEventListener('popstate',event=>{if(ready)navigate(location.href,'section',{pop:true,scroll:event.state?.scroll||[0,0]})});
 let scrollFrame;
 window.addEventListener('scroll',()=>{if(!scrollFrame)scrollFrame=requestAnimationFrame(()=>{scrollFrame=null;snapshotScroll()})},{passive:true});
 const standalone=()=>matchMedia('(display-mode: standalone)').matches||navigator.standalone;
 let wasHidden=document.hidden;
 document.addEventListener('visibilitychange',()=>{
  if(document.hidden){wasHidden=true;clearCache()}
  else if(wasHidden){wasHidden=false;if(standalone())resetPlanningWeek(true);else if(ready)navigate(location.href,'section',{replace:true,force:true,scroll:[scrollX,scrollY]})}
 });
 window.addEventListener('pageshow',event=>{if(event.persisted&&standalone())resetPlanningWeek(true)});
 document.addEventListener('DOMContentLoaded',()=>{
  user=document.body.dataset.userId;version=document.body.dataset.viewVersion;ready=Boolean(user&&appURL(location.href));renderedURL=location.href;
  if(ready){history.scrollRestoration='manual';history.replaceState({study:true,scroll:[scrollX,scrollY]},'',location.href);schedulePrefetch()}
 });
 return {navigate,prefetch,clearCache,get ready(){return ready}};
})();
