// Each view has its own listener lifetime; the document and router persist.
window.initializeStudyView = function initializeStudyView() {
 window.disposeStudyView?.();
 const lifecycle = new AbortController();
 window.disposeStudyView = () => lifecycle.abort();
 function onGlobal(target, type, handler, options = {}) {
  target.addEventListener(type, handler, {...options, signal:lifecycle.signal});
 }
document.querySelectorAll('.add-row').forEach(button=>button.addEventListener('click',()=>{const body=button.closest('form').querySelector('tbody');const row=body.lastElementChild.cloneNode(true);row.querySelectorAll('input').forEach(i=>i.value='');body.append(row)}));
document.querySelectorAll('[data-confirm]').forEach(form=>form.addEventListener('submit',event=>{if(!confirm(form.dataset.confirm))event.preventDefault()}));
const dataNode=document.getElementById('analytics-data');
function chart(container,rows,series,max){
 const width=Math.min(700,Math.max(280,container.clientWidth)),right=width-20;
 const ns='http://www.w3.org/2000/svg', svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${width} 230`);svg.setAttribute('role','img');svg.setAttribute('aria-label','График результатов по датам');
 function el(tag,attrs,text){const node=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>node.setAttribute(k,v));if(text!==undefined)node.textContent=text;svg.append(node);return node;}
 for(let n=0;n<=4;n++){const y=190-n*40;el('line',{x1:40,x2:right,y1:y,y2:y,stroke:'var(--line)'});el('text',{x:3,y:y+4,'font-size':13,fill:'var(--muted)'},Math.round(max*n/4));}
 const times=rows.map(row=>Date.parse(`${row.day}T00:00:00Z`)),first=times[0],last=times.at(-1);
 const x=i=>last===first?(38+right)/2:38+(times[i]-first)*(right-38)/(last-first), y=v=>190-v/max*160;
 series.forEach(([key,label,color])=>{let segment=[];const flush=()=>{if(segment.length)el('polyline',{points:segment.join(' '),fill:'none',stroke:color,'stroke-width':2});segment=[];};rows.forEach((row,i)=>{const value=row[key];if(value===null||value===undefined){flush();return}segment.push(`${x(i)},${y(value)}`);const point=el('circle',{cx:x(i),cy:y(value),r:4,fill:color});const title=document.createElementNS(ns,'title');title.textContent=`${row.day}: ${label} ${value}`;point.append(title)});flush();});
 const labelDays=[...new Set(rows.map(row=>row.day))];
 rows.forEach((row,i)=>{if((row.day===labelDays[0]||row.day===labelDays.at(-1)||row.day===labelDays[Math.floor((labelDays.length-1)/2)]||labelDays.length<5)&&rows.findIndex(r=>r.day===row.day)===i)el('text',{x:x(i),y:218,'text-anchor':'middle','font-size':12,fill:'var(--muted)'},row.day.slice(5).split('-').reverse().join('.'))});container.append(svg);
 const legend=document.createElement('p');legend.className='chart-legend';series.forEach(([,label,color])=>{const item=document.createElement('span'),line=document.createElement('i');line.style.background=color;item.append(line,document.createTextNode(label));legend.append(item)});container.append(legend);
}
const themeButton=document.getElementById('theme-toggle');
function updateThemeButton(){const dark=document.documentElement.dataset.theme==='dark';if(themeButton){themeButton.textContent=dark?'Светлая тема':'Тёмная тема';themeButton.setAttribute('aria-pressed',String(dark));}}
updateThemeButton();
themeButton?.addEventListener('click',()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';try{localStorage.setItem('theme',document.documentElement.dataset.theme)}catch(e){}updateThemeButton()});
onGlobal(matchMedia('(prefers-color-scheme: dark)'),'change',event=>{if(document.documentElement.dataset.fixedTheme)return;try{if(localStorage.getItem('theme'))return}catch(e){}document.documentElement.dataset.theme=event.matches?'dark':'light';updateThemeButton()});
document.querySelectorAll('form:not([data-goal-selection])').forEach(form=>{
 const subject=form.querySelector('select[name="subject_id"]');if(!subject)return;
 const task=form.querySelector('select[name="task_id"]'),kind=form.querySelector('select[name="kind"]'),fieldset=form.querySelector('fieldset');
 function sync(){
  form.querySelectorAll('[data-subject]').forEach(node=>{const visible=node.dataset.subject===subject.value;node.hidden=!visible;const input=node.matches('option')?node:node.querySelector('input');input.disabled=!visible;if(!visible){if(input.matches('option'))input.selected=false;else input.checked=false}});
  if(task){const visible=kind?.value==='task';task.closest('label').hidden=!visible;task.disabled=!visible;task.required=visible;if(!visible)task.value='';}
  if(fieldset){const visible=!!subject.value&&kind?.value!=='rest';fieldset.hidden=!visible;fieldset.querySelectorAll('input').forEach(input=>{if(!visible){input.disabled=true;input.checked=false}})}
  if(kind?.querySelector('option[value="rest"]')){const rest=kind.value==='rest';subject.closest('label').hidden=rest;subject.required=kind.value==='exam';if(rest)subject.value='';}
 }
 subject.addEventListener('change',sync);kind?.addEventListener('change',sync);sync();
});
document.getElementById('student-switch')?.addEventListener('change',event=>{const url=new URL(location.href);url.searchParams.set('student',event.target.value);navigateWithMotion(url.toString())});
document.querySelectorAll('table').forEach(table=>{const wrapper=document.createElement('div');wrapper.className='table-scroll';table.before(wrapper);wrapper.append(table)});
const addButton=document.getElementById('board-add'),createPanel=document.getElementById('board-create');
if(addButton&&createPanel){
 const label=addButton.getAttribute('aria-label');
 if(createPanel instanceof HTMLDialogElement){
  addButton.addEventListener('click',()=>{
   if(createPanel.open)return;
   createPanel.showModal();document.documentElement.classList.add('modal-open');
   addButton.setAttribute('aria-expanded','true');
   createPanel.querySelector('select,input:not([type="hidden"])')?.focus();
  });
  createPanel.querySelector('.dialog-close').addEventListener('click',()=>createPanel.close());
  createPanel.addEventListener('click',event=>{
   if(event.target!==createPanel)return;
   const rect=createPanel.getBoundingClientRect();
   if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)createPanel.close();
  });
  createPanel.addEventListener('close',()=>{
   document.documentElement.classList.remove('modal-open');
   addButton.setAttribute('aria-expanded','false');addButton.focus({preventScroll:true});
  });
 }else{
  addButton.addEventListener('click',()=>{
   const opening=createPanel.hidden;createPanel.hidden=!opening;
   addButton.setAttribute('aria-expanded',String(opening));addButton.setAttribute('aria-label',opening?'Закрыть':label);addButton.title=opening?'Закрыть':label;addButton.querySelector('.material-symbols-outlined').textContent=opening?'close':'add';
   if(opening){createPanel.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'center'});createPanel.querySelector('input:not([type="hidden"]),select')?.focus({preventScroll:true});}
  });
 }
}

document.querySelectorAll('.profile-avatar').forEach(img=>{
 const recover=()=>{const fallback=img.dataset.fallback;if(fallback){delete img.dataset.fallback;img.src=fallback}else img.hidden=true};
 img.addEventListener('error',recover);if(img.complete&&!img.naturalWidth)recover();
});

document.querySelectorAll('form[data-goal-selection]').forEach(form=>{
 const subject=form.querySelector('[name="subject_id"]'),mode=form.querySelector('[name="kind"]'),group=form.querySelector('[name="group_id"]'),picker=form.querySelector('[data-goal-tasks]');
 function sync(){
  const useGroup=mode.value==='group';
  group.querySelectorAll('option[data-subject]').forEach(option=>{const active=option.dataset.subject===subject.value;option.hidden=!active;option.disabled=!active;if(!active)option.selected=false});
  group.required=useGroup;group.disabled=!useGroup;
  form.querySelectorAll('[data-group-field]').forEach(label=>{label.hidden=!useGroup;label.querySelector('select').disabled=!useGroup});
  picker.hidden=useGroup||!subject.value;
  picker.querySelectorAll('[data-subject]').forEach(label=>{const active=!useGroup&&label.dataset.subject===subject.value;label.hidden=!active;const input=label.querySelector('input');input.disabled=!active;if(!active)input.checked=false});
  mode.setCustomValidity('');
 }
 subject.addEventListener('change',sync);mode.addEventListener('change',sync);
 picker.addEventListener('change',()=>mode.setCustomValidity(''));
 form.addEventListener('submit',event=>{if(mode.value==='tasks'&&!picker.querySelector('input:checked:not(:disabled)')){event.preventDefault();mode.setCustomValidity('Выберите хотя бы одно задание');mode.reportValidity()}});
 sync();
});

if(dataNode){
 const data=JSON.parse(dataNode.textContent),root=document.querySelector('.analytics-layout');
 const today=root.dataset.today,period=document.getElementById('analytics-period');
 const dayDate=day=>new Date(`${day}T00:00:00Z`),iso=date=>date.toISOString().slice(0,10);
 const moveDay=(day,n)=>{const date=dayDate(day);date.setUTCDate(date.getUTCDate()+n);return iso(date)};
 const shortDate=day=>dayDate(day).toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit',timeZone:'UTC'});
 const earliest=data.rhythm.length?data.rhythm.reduce((day,row)=>row.day<day?row.day:day,today):today;
 const startDay=()=>period.value==='all'?earliest:moveDay(today,1-Number(period.value));
 const inPeriod=row=>row.day>=startDay()&&row.day<=today;
 const average=values=>values.length?values.reduce((sum,value)=>sum+value,0)/values.length:0;
 const number=value=>Number.isInteger(value)?String(value):value.toFixed(1).replace('.',',');
 function metrics(id,items){
  const container=document.getElementById(id);container.replaceChildren();
  items.forEach(([value,label,note])=>{const item=document.createElement('div');item.className='metric';const strong=document.createElement('strong');strong.textContent=value;const text=document.createElement('span');text.textContent=label;item.append(strong,text);if(note){const small=document.createElement('small');small.textContent=note;item.append(small)}container.append(item)});
 }
 function empty(container,text){const p=document.createElement('p');p.className='chart-empty';p.textContent=text;container.append(p)}
 function draw(container,rows,series,max,label,emptyText=label){
  container.replaceChildren();if(!rows.length){empty(container,emptyText);return}
  chart(container,rows,series,max);
  container.querySelector('svg').setAttribute('aria-label',label);
 }
 function rhythm(){
  const subject=document.getElementById('rhythm-subject').value;
  const all=data.rhythm.filter(row=>subject==='all'||String(row.subject_id)===subject),rows=all.filter(inPeriod),days=new Set(rows.map(row=>row.day));
  const everyDay=new Set(all.map(row=>row.day));let streak=0,cursor=everyDay.has(today)?today:moveDay(today,-1);
  while(everyDay.has(cursor)){streak++;cursor=moveDay(cursor,-1)}
  metrics('rhythm-metrics',[[days.size,'Дней с занятиями'],[rows.length,'Записанных работ'],[streak,'Дней подряд']]);
  const calendar=document.getElementById('rhythm-calendar');calendar.replaceChildren();
  const labels=document.createElement('div');labels.className='calendar-day-labels';['Пн','','Ср','','Пт','','Вс'].forEach(text=>{const label=document.createElement('span');label.textContent=text;labels.append(label)});calendar.append(labels);
  const grid=document.createElement('div');grid.className='activity-calendar';
  const start=startDay(),weekday=(dayDate(start).getUTCDay()+6)%7;let week=moveDay(start,-weekday);
  const counts=new Map();rows.forEach(row=>counts.set(row.day,(counts.get(row.day)||0)+1));
  while(week<=today){const column=document.createElement('div');column.className='calendar-week';for(let i=0;i<7;i++){
   const day=moveDay(week,i),cell=document.createElement('span'),count=counts.get(day)||0;
   cell.className='calendar-day'+(day<start||day>today?' outside':count?' studied':'');
   cell.setAttribute('role','img');cell.setAttribute('aria-label',`${shortDate(day)}: ${count?`работ ${count}`:'без занятий'}`);cell.title=cell.getAttribute('aria-label');
   if(count>1)cell.style.opacity=String(Math.min(1,.55+count*.15));column.append(cell);
  }grid.append(column);week=moveDay(week,7)}calendar.append(grid);
  document.getElementById('rhythm-range').textContent=`${shortDate(start)} — ${shortDate(today)}`;
 }
 function exams(){
  const subject=document.getElementById('exam-subject').value,all=data.exams.filter(row=>String(row.subject_id)===subject&&inPeriod(row));
  const rows=all.filter(row=>row.secondary!==null),last=rows.at(-1),first=rows[0],delta=rows.length>1?last.secondary-first.secondary:null;
  metrics('exam-metrics',[[last?number(last.secondary):'—','Последний балл',last?shortDate(last.day):''],[rows.length?number(average(rows.map(row=>row.secondary))):'—','Средний балл'],[delta===null?'—':`${delta>0?'+':''}${number(delta)}`,'Изменение',rows.length>1?'За выбранный период':'']]);
  // Keep gaps where an exam has no converted score; never substitute its primary score
  const chartRows=all.map(row=>({day:row.day,secondary:row.secondary}));
  draw(document.getElementById('exam-chart'),rows.length?chartRows:[],[['secondary','Баллы из 100','var(--accent)']],100,'Динамика баллов пробников по шкале 0–100','Пока нет пробников с баллом по шкале');
  const pending=document.getElementById('exam-pending'),missing=all.length-rows.length;pending.hidden=!missing;
  pending.querySelector('span').textContent=`Без балла по шкале: ${missing}`;
 }
 let selectedTask=null;
 function results(taskId){
  const subject=document.getElementById('task-subject').value,kind=document.getElementById('task-kind').value;
  return data.tasks.filter(row=>row.id===taskId&&String(row.subject_id)===subject&&(kind==='all'||row.kind===kind)).flatMap(row=>row.results).filter(inPeriod).sort((a,b)=>a.day.localeCompare(b.day)||a.activity_id-b.activity_id);
 }
 function taskDetail(){
  const task=data.task_catalog.find(row=>row.id===selectedTask),rows=results(selectedTask),attempts=rows.filter(row=>row.attempted);
  const correct=attempts.filter(row=>row.score===row.maximum).length,partial=attempts.filter(row=>row.score>0&&row.score<row.maximum).length,zero=attempts.length-correct-partial,skipped=rows.length-attempts.length;
  document.getElementById('selected-task').textContent=task?.name||'Нет заданий';
  document.getElementById('task-last').textContent=attempts.length?`Последнее решение ${shortDate(attempts.at(-1).day)}`:'';
  const accuracy=attempts.length?Math.round(correct/attempts.length*100):null,mastery=attempts.length?Math.round(average(attempts.map(row=>row.score/row.maximum))*100):null;
  metrics('task-metrics',[[accuracy===null?'—':`${accuracy}%`,'Правильных решений'],[correct,'Решено полностью'],[attempts.length,'Всего попыток'],[mastery===null?'—':`${mastery}%`,'Средний результат','Доля от максимального балла']]);
  const chartRows=attempts.map((row,index)=>({day:row.day,result:Math.round(row.score/row.maximum*100),trend:Math.round(average(attempts.slice(Math.max(0,index-4),index+1).map(r=>r.score/r.maximum))*100)}));
  draw(document.getElementById('task-chart'),chartRows,[['result','Результат решения','var(--muted)'],['trend','Среднее последних 5','var(--accent)']],100,'Динамика результата задания, в процентах от максимального балла');
  if(!attempts.length){document.getElementById('task-chart').replaceChildren();empty(document.getElementById('task-chart'),'По этому заданию пока нет решений')}
  const breakdown=document.getElementById('task-breakdown');breakdown.replaceChildren();
  const bar=document.createElement('div');bar.className='result-bar';bar.setAttribute('aria-hidden','true');
  const values=[[correct,'Полностью правильно','full'],[partial,'Частично правильно','partial'],[zero,'Без баллов','zero']];
  values.forEach(([count,label,style])=>{if(count&&attempts.length){const segment=document.createElement('span');segment.className=style;segment.style.width=`${count/attempts.length*100}%`;bar.append(segment)}});breakdown.append(bar);
  [...values,[skipped,'Не решалось','skipped']].forEach(([count,label,style])=>{const line=document.createElement('div');line.className=`breakdown-row ${style}`;const name=document.createElement('span');name.textContent=label;const value=document.createElement('strong');value.textContent=count;line.append(name,value);breakdown.append(line)});
 }
 function taskPicker(){
  const subject=document.getElementById('task-subject').value,tasks=data.task_catalog.filter(row=>String(row.subject_id)===subject),picker=document.getElementById('task-picker');
  if(!tasks.some(row=>row.id===selectedTask))selectedTask=tasks[0]?.id??null;
  picker.replaceChildren();const numberSelect=document.getElementById('task-number');numberSelect.replaceChildren();tasks.forEach(task=>{const option=document.createElement('option');option.value=task.id;option.textContent=task.name;option.selected=task.id===selectedTask;numberSelect.append(option)});numberSelect.disabled=!tasks.length;tasks.forEach(task=>{const button=document.createElement('button');button.type='button';button.className='task-chip';button.setAttribute('aria-pressed',String(selectedTask===task.id));const name=document.createElement('span');name.textContent=task.name;const rows=results(task.id).filter(row=>row.attempted),percent=rows.length?Math.round(rows.filter(row=>row.score===row.maximum).length/rows.length*100):null;const value=document.createElement('small');value.textContent=percent===null?'—':`${percent}%`;button.append(name,value);button.setAttribute('aria-label',`${task.name}: ${percent===null?'нет решений':`${percent}% правильных`}`);button.addEventListener('click',()=>{selectedTask=task.id;taskPicker()});picker.append(button)});
  taskDetail();
 }
 function wellbeing(){const container=document.getElementById('wellbeing-chart');if(container)draw(container,data.wellbeing.filter(inPeriod),[['mood','Самочувствие','var(--accent)'],['load','Нагрузка','var(--muted)']],5,'Самочувствие и нагрузка по датам')}
 function render(){rhythm();exams();taskPicker();wellbeing()}
 period.addEventListener('change',()=>{const url=new URL(location.href);url.searchParams.set('period',period.value);navigateWithMotion(url.toString())});
 document.getElementById('rhythm-subject').addEventListener('change',rhythm);
 document.getElementById('exam-subject').addEventListener('change',exams);
 document.getElementById('task-subject').addEventListener('change',taskPicker);
 document.getElementById('task-kind').addEventListener('change',taskPicker);
 document.getElementById('task-number').addEventListener('change',event=>{selectedTask=Number(event.target.value);taskPicker()});
 onGlobal(window,'resize',render);
 render();
}

// Manual converted scores and historical scales are separate editing modes
document.querySelectorAll('[data-exam-edit]').forEach(form=>{
 const mode=form.querySelector('[name="conversion"]'),label=form.querySelector('[data-manual-secondary]'),input=label.querySelector('input');
 const sync=()=>{const manual=mode.value==='manual';label.hidden=!manual;input.disabled=!manual};
 mode.addEventListener('change',sync);sync();
});


const installButton = document.getElementById('install-app');
if (installButton) installButton.hidden = !window.studyInstallPrompt;
installButton?.addEventListener('click', async () => {
 if (!window.studyInstallPrompt) return;
 await window.studyInstallPrompt.prompt(); window.studyInstallPrompt = null; installButton.hidden = true;
});

// Keep desktop controls in the header and place them in mobile settings.
const settingsLayout = matchMedia('(max-width: 750px)');
const settingsControls = document.getElementById('mobile-settings-controls');
const headerControls = [...document.querySelectorAll('header > .theme-toggle, header > .profile-cards')].map(node => {
 const marker = document.createComment('desktop-control'); node.before(marker); return {node, marker};
});
function syncSettingsControls() {
 headerControls.forEach(({node, marker}) => {
  if (settingsLayout.matches && settingsControls) settingsControls.append(node);
  else marker.after(node);
 });
}
onGlobal(settingsLayout,'change', syncSettingsControls); syncSettingsControls();
const currentSection = document.querySelector('header nav [aria-current="page"]');
if (settingsLayout.matches && currentSection) currentSection.scrollIntoView({block:'nearest', inline:'center'});

// Switch weeks on deliberate horizontal gestures, without intercepting forms or scrolling.
const planningBoard = document.querySelector('.planning-board');
if (planningBoard) {
 const mobileWeek = matchMedia('(max-width: 750px)');
 const weekControl = document.querySelector('.week-switch');
 function moveWeek(next) {
  if (!mobileWeek.matches || document.querySelector('dialog[open]')) return;
  const link = weekControl?.querySelector(next ? 'a:last-child' : 'a:first-child');
  if (link) navigateWithMotion(link.href, next ? 'next' : 'previous');
 }
 let gesture;
 const excluded = 'a,button,input,select,textarea,form,dialog,.table-scroll';
 function startWeekSwipe(event) {
  gesture = null;
  if (!mobileWeek.matches || event.touches.length !== 1 || event.target.closest(excluded)) return;
  const touch = event.touches[0];
  // Leave the screen edges available for system back/forward gestures.
  if (touch.clientX < 24 || touch.clientX > innerWidth - 24) return;
  gesture = {x:touch.clientX, y:touch.clientY, time:performance.now()};
 }
 function finishWeekSwipe(event) {
  const start = gesture; gesture = null;
  if (!start || event.touches.length || event.changedTouches.length !== 1) return;
  const touch = event.changedTouches[0], dx = touch.clientX-start.x, dy = touch.clientY-start.y;
  if (Math.abs(dx) >= 56 && Math.abs(dx) > Math.abs(dy)*1.5 && performance.now()-start.time < 1000) moveWeek(dx < 0);
 }
 [planningBoard, weekControl].filter(Boolean).forEach(node => {
  node.addEventListener('touchstart', startWeekSwipe, {passive:true});
  node.addEventListener('touchend', finishWeekSwipe, {passive:true});
  node.addEventListener('touchcancel', () => {gesture=null}, {passive:true});
 });
 function syncWeekKeyboard() {
  if (mobileWeek.matches) planningBoard.tabIndex = 0;
  else planningBoard.removeAttribute('tabindex');
 }
 onGlobal(mobileWeek,'change', syncWeekKeyboard); syncWeekKeyboard();
 planningBoard.addEventListener('keydown', event => {
  if (event.target !== planningBoard || !['ArrowLeft','ArrowRight'].includes(event.key) || !mobileWeek.matches) return;
  event.preventDefault(); moveWeek(event.key === 'ArrowRight');
 });
}

const weekPicker = document.getElementById('week-picker');
const weekToggle = document.getElementById('week-picker-toggle');
if (weekPicker && weekToggle) {
 const list = document.getElementById('week-options'), dateInput = document.getElementById('week-picker-date');
 const selected = document.querySelector('.week-switch time').dateTime;
 const iso = day => day.toISOString().slice(0,10);
 const dayDate = text => new Date(`${text}T00:00:00Z`);
 const addDays = (day,n) => new Date(day.getTime()+n*86400000);
 const monday = day => addDays(day,-((day.getUTCDay()+6)%7));
 const minimum = '2000-01-10', maximum = '2099-12-28';
 const current = iso(monday(dayDate(weekPicker.dataset.today)));
 const format = new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric',timeZone:'UTC'});
 let center = selected;
 function renderWeeks(focusSelected = false) {
  list.replaceChildren();
  for (let n=-8;n<=8;n++) {
   const day = addDays(dayDate(center),n*7), value = iso(day);
   if (value < minimum || value > maximum) continue;
   const link = document.createElement('a'), url = new URL(document.querySelector('.week-switch a').href,location.href);
   url.searchParams.set('week',value); link.href=url.href;
   const label=document.createElement('span'); label.textContent=`${format.format(day)} — ${format.format(addDays(day,6))}`;link.append(label);
   if(value===selected)link.setAttribute('aria-current','date');
   if(value===current){const badge=document.createElement('small');badge.textContent='Текущая';link.append(badge)}
   list.append(link);
  }
  document.getElementById('weeks-earlier').disabled = center <= minimum;
  document.getElementById('weeks-later').disabled = center >= maximum;
  const target=list.querySelector('[aria-current="date"]') || list.children[Math.floor(list.children.length/2)];
  if (target) {list.scrollTop=target.offsetTop-list.clientHeight/2+target.clientHeight/2;if(focusSelected)target.focus({preventScroll:true})}
 }
 function closeWeeks(){weekPicker.close()}
 weekToggle.addEventListener('click',()=>{
  center=selected;dateInput.value=selected;weekPicker.showModal();document.documentElement.classList.add('modal-open');weekToggle.setAttribute('aria-expanded','true');renderWeeks(true);
 });
 document.getElementById('week-picker-close').addEventListener('click',closeWeeks);
 weekPicker.addEventListener('close',()=>{document.documentElement.classList.remove('modal-open');weekToggle.setAttribute('aria-expanded','false')});
 weekPicker.addEventListener('click',event=>{
  if(event.target.closest('.week-list a'))closeWeeks();
  if(event.target!==weekPicker)return;
  const rect=weekPicker.getBoundingClientRect();
  if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeWeeks();
 });
 function shiftWeeks(amount){center=iso(addDays(dayDate(center),amount*7));center=center<minimum?minimum:center>maximum?maximum:center;renderWeeks()}
 document.getElementById('weeks-earlier').addEventListener('click',()=>shiftWeeks(-16));
 document.getElementById('weeks-later').addEventListener('click',()=>shiftWeeks(16));
 document.getElementById('weeks-today').addEventListener('click',()=>{center=current;dateInput.value=current;renderWeeks(true)});
 dateInput.addEventListener('change',()=>{if(!dateInput.value||!dateInput.checkValidity())return;center=iso(monday(dayDate(dateInput.value)));renderWeeks()});
}

};
window.initializeStudyView();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js', {updateViaCache:'none'}).catch(() => {});
window.addEventListener('beforeinstallprompt', event => {
 event.preventDefault(); window.studyInstallPrompt = event;
 const button = document.getElementById('install-app'); if (button) button.hidden = false;
});
window.addEventListener('appinstalled', () => {
 window.studyInstallPrompt = null;
 const button = document.getElementById('install-app'); if (button) button.hidden = true;
});
