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
