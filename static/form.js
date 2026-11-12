const host=document.getElementById('performances');
let count=0;
const specialties=['Tessuto Aereo','Cerchio Aereo','Amaca','Palo Aereo','Bungee Dance','Pole Dance','Arti Varie'];
const ages=['Baby (4–7)','Kids (8–10)','Teen (11–13)','Juniores (14–16)','Seniores (17–25)','Over 26'];
const levels=['Principiante','Intermedio','Avanzato','Istruttore'];
function options(items){return items.map(x=>`<option>${x}</option>`).join('')}
function renderAthletes(card){
 const n=card.querySelector('.section').value==='Duo'?2:1,rows=card.querySelector('.athlete-rows');
 const previous=[...rows.querySelectorAll('.athlete-row')].map(r=>({name:r.querySelector('.athlete-name')?.value||'',dob:r.querySelector('.athlete-dob')?.value||''}));
 rows.innerHTML='';
 for(let i=0;i<n;i++){
  const v=previous[i]||{},row=document.createElement('div');row.className='athlete-row';
  row.innerHTML=`<b>Atleta ${i+1}</b><label>Nome e cognome<input class="athlete-name" required autocomplete="name"></label><label>Data di nascita<input class="athlete-dob" type="date" required max="2027-01-30"></label>`;
  row.querySelector('.athlete-name').value=v.name;row.querySelector('.athlete-dob').value=v.dob;rows.append(row);
 }
}
function applyRules(card){
 const section=card.querySelector('.section'),specialty=card.querySelector('.specialty');
 const duo=section.querySelector('option[value="Duo"]'),soloOnly=['Bungee Dance','Pole Dance'].includes(specialty.value);
 duo.disabled=soloOnly;if(soloOnly)section.value='Assolo';renderAthletes(card);
}
function add(){
 count++;const card=document.createElement('div');card.className='performance';
 card.innerHTML=`<div class="performance-head"><b>Esibizione ${count}</b><button type="button" class="secondary remove">Rimuovi</button></div><label>Sezione<select class="section"><option>Assolo</option><option>Duo</option></select></label><label>Specialità<select class="specialty">${options(specialties)}</select></label><label>Categoria<select class="age">${options(ages)}</select></label><label>Livello<select class="level">${options(levels)}</select></label><label>Titolo coreografia<input class="title" required></label><label>Musica (MP3, massimo 2:30)<input class="music" type="file" name="music_files" accept=".mp3,audio/mpeg" required></label><div class="athlete-rows"></div>`;
 host.append(card);renderAthletes(card);
 card.querySelector('.remove').onclick=()=>{card.remove();update()};
 card.querySelector('.specialty').onchange=()=>{applyRules(card);update()};
 card.querySelector('.section').onchange=()=>{renderAthletes(card);update()};
 card.querySelectorAll('select,input').forEach(x=>x.addEventListener('change',update));
 card.querySelectorAll('input').forEach(x=>x.addEventListener('input',update));update();
}
function update(){
 let total=0;const data=[...host.querySelectorAll('.performance')].map(card=>{
  const section=card.querySelector('.section').value;total+=section==='Assolo'?60:80;
  const athletes=[...card.querySelectorAll('.athlete-row')].map(row=>({name:row.querySelector('.athlete-name').value,dob:row.querySelector('.athlete-dob').value}));
  return{section,specialty:card.querySelector('.specialty').value,category:card.querySelector('.age').value,level:card.querySelector('.level').value,title:card.querySelector('.title').value,athletes};
 });
 document.getElementById('total').textContent=total+' €';document.getElementById('performance_data').value=JSON.stringify(data);
}
document.getElementById('add').onclick=add;
document.getElementById('payment').onchange=e=>document.getElementById('bank').hidden=e.target.value!=='bonifico';
document.getElementById('reg').onsubmit=e=>{update();if(!host.children.length){e.preventDefault();alert('Aggiungi almeno un’esibizione.')}};
add();
