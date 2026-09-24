import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';

function App() {
  const [key,setKey]=useState(''), [admin,setAdmin]=useState(''), [actor,setActor]=useState('');
  const [assignment,setAssignment]=useState(null), [message,setMessage]=useState(''), [busy,setBusy]=useState(false);
  const [text,setText]=useState(''), [split,setSplit]=useState('pool'), [gold,setGold]=useState('');
  const [disagreements,setDisagreements]=useState([]), [report,setReport]=useState(null);
  async function api(path, data, method='POST') {
    const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-API-Key':key,'X-Admin-Key':admin},...(data===undefined?{}:{body:JSON.stringify(data)})});
    const body=await response.json();
    if(!response.ok) throw Error(typeof body.detail==='string'?body.detail:JSON.stringify(body.detail));
    return body;
  }
  async function action(work) {setBusy(true);setMessage('');try {await work();}catch(error){setMessage(error.message);}finally{setBusy(false);}}
  return <main><h1>LabelDesk</h1><p>Разметка текстов двумя участниками и разбор разногласий.</p>
    <section><h2>Доступ</h2><label>API-ключ<input type="password" value={key} onChange={e=>setKey(e.target.value)}/></label>
      <label>Ваш идентификатор<input value={actor} onChange={e=>setActor(e.target.value)} disabled={!!assignment}/></label>
      <label>Ключ администратора<input type="password" value={admin} onChange={e=>setAdmin(e.target.value)}/></label>
      <small>Ключи хранятся только в памяти этой страницы. Классы: 0 — негативный, 1 — позитивный.</small></section>
    <section><h2>Разметка</h2><button disabled={busy||!!assignment||!actor} onClick={()=>action(async()=>{const r=await api('/claim',{actor});setAssignment(r.task?{...r,actor}:null);if(!r.task)setMessage('Доступных заданий пока нет.');})}>Получить задание</button>
      {assignment&&<><blockquote>{assignment.task.text}</blockquote><p>Назначение действует 10 минут.</p>
      {[0,1].map(label=><button key={label} disabled={busy} onClick={()=>action(async()=>{await api('/annotations',{actor:assignment.actor,task_id:assignment.task.id,token:assignment.token,label});setAssignment(null);setMessage('Аннотация сохранена.');})}>{label===0?'Негативный':'Позитивный'}</button>)}
      <button disabled={busy} onClick={()=>setAssignment(null)}>Убрать с экрана</button></>}
    </section>
    {admin&&<section><h2>Управление разметкой</h2>
      <label>Новый текст<textarea value={text} onChange={e=>setText(e.target.value)}/></label>
      <label>Выборка<select value={split} onChange={e=>setSplit(e.target.value)}><option value="pool">Пул разметки</option><option value="test">Независимый тест</option></select></label>
      <label>Эталон для симуляции<select value={gold} onChange={e=>setGold(e.target.value)}><option value="">Не задан</option><option value="0">0</option><option value="1">1</option></select></label>
      <button disabled={busy||!text.trim()} onClick={()=>action(async()=>{await api('/tasks',{text,split,gold:gold===''?null:Number(gold)});setText('');setMessage('Текст добавлен.');})}>Добавить</button>
      <button disabled={busy} onClick={()=>action(async()=>setDisagreements(await api('/disagreements',undefined,'GET')))}>Показать разногласия</button>
      {disagreements.map(task=><article key={task.id}><p>{task.text}</p>{[0,1].map(label=><button key={label} disabled={busy} onClick={()=>action(async()=>{await api(`/tasks/${task.id}/resolve`,{label,revision:task.revision});setDisagreements(await api('/disagreements',undefined,'GET'));})}>Утвердить {label}</button>)}</article>)}
      <button disabled={busy} onClick={()=>action(async()=>setReport(await api('/uncertain',undefined,'GET')))}>Неопределённые примеры</button>
      <button disabled={busy} onClick={()=>action(async()=>setReport(await api('/simulate?budget=12',{})))}>Сравнить стратегии: 12 меток</button>
      {report&&<pre>{JSON.stringify(report,null,2)}</pre>}
    </section>}
    <p role="status" aria-live="polite">{busy?'Выполняется запрос…':message}</p>
  </main>;
}
createRoot(document.getElementById('root')).render(<App/>);
