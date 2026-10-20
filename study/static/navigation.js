// A new launch or reload opens the current week; links keep the selected week.
function resetPlanningWeek(refresh = false) {
 const url = new URL(location.href);
 if (!['/goals', '/schedule', '/plan', '/editing'].includes(url.pathname)) return;
 if (!url.searchParams.has('week')) { if (refresh) location.reload(); return; }
 url.searchParams.delete('week');
 url.searchParams.delete('edit');
 location.replace(url.href);
}
if (performance.getEntriesByType('navigation')[0]?.type === 'reload') resetPlanningWeek();
const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone;
let wasHidden = document.hidden;
document.addEventListener('visibilitychange', () => {
 if (document.hidden) wasHidden = true;
 else if (wasHidden && standalone()) { wasHidden = false; resetPlanningWeek(true); }
});
window.addEventListener('pageshow', event => { if (event.persisted && standalone()) resetPlanningWeek(true); });

// Carry a short entry direction across ordinary server-rendered page loads.
try {
 const motion = JSON.parse(sessionStorage.getItem('page-motion') || 'null');
 sessionStorage.removeItem('page-motion');
 if (motion && motion.url === location.href && Date.now()-motion.time < 5000) document.documentElement.dataset.navigation = motion.direction;
} catch (_) {}
let pageNavigationPending = false;
function navigateWithMotion(destination, direction = 'section') {
 if (pageNavigationPending) return;
 const url = new URL(destination, location.href);
 if (url.origin !== location.origin || matchMedia('(prefers-reduced-motion: reduce)').matches) { location.assign(url.href); return; }
 pageNavigationPending = true;
 try { sessionStorage.setItem('page-motion', JSON.stringify({url:url.href, direction, time:Date.now()})); } catch (_) {}
 const target = direction === 'section' ? document.querySelector('main') : document.querySelector('.card-board');
 if (!target?.animate) { location.assign(url.href); return; }
 const frames = direction === 'section' ? [{opacity:1},{opacity:0}] : [{opacity:1,transform:'translateX(0)'},{opacity:0,transform:`translateX(${direction === 'next' ? -16 : 16}px)`}];
 const animation = target.animate(frames, {duration:120, easing:'ease-out', fill:'forwards'});
 animation.id = 'page-exit';
 Promise.race([animation.finished.catch(()=>{}),new Promise(resolve=>setTimeout(resolve,200))]).then(()=>location.assign(url.href));
}
window.addEventListener('pageshow', () => {
 pageNavigationPending = false;
 document.querySelectorAll('main,.card-board').forEach(node=>node.getAnimations().filter(animation=>animation.id==='page-exit').forEach(animation=>animation.cancel()));
});
document.addEventListener('click', event => {
 const link = event.target.closest('header nav a,.week-switch a,.week-list a');
 if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return;
 event.preventDefault();
 const oldWeek = document.querySelector('.week-switch time')?.dateTime;
 const nextWeek = new URL(link.href).searchParams.get('week');
 const direction = link.closest('header nav') || !oldWeek || !nextWeek ? 'section' : nextWeek > oldWeek ? 'next' : 'previous';
 navigateWithMotion(link.href, direction);
});
