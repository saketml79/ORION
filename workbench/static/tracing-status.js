/* Configuration read only. Never asks the browser for tracing credentials. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const page = document.getElementById('page-agents');
  if (!page) return;
  const element = (tag, text) => {const e=document.createElement(tag); e.textContent=text; return e;};
  const panel=element('section',''); panel.className='disclosure'; panel.id='langsmith-panel';
  panel.append(element('h3','Tracing integrations'));
  const status=element('p','Configuration not checked.'); status.setAttribute('role','status');
  const button=element('button','Refresh tracing configuration'); button.type='button'; button.className='secondary compact';
  panel.append(status,button,element('p','Tracing records payload-free execution spans. Prompts, XML, SQL, documents and tool arguments/results are excluded. Exported rubric scores are advisory checks, not migration parity or an independent evaluation experiment.'));
  panel.append(element('p','MLflow: Not integrated with this app’s agent tracing. No MLflow trace or evaluation delivery is claimed.'));
  page.querySelector('#agent-availability').after(panel);
  const selection=()=>JSON.stringify([document.getElementById('project-select').value,
    document.getElementById('estate-package-select')?.value || document.getElementById('project-package').value]);
  let epoch=0, context=selection(), pending=null, controller=null;
  function invalidate() {
    const next=selection();
    if(next===context) return;
    context=next; epoch+=1; controller?.abort(); pending=null; button.disabled=false;
    status.textContent='Selection changed. Tracing configuration is unknown until read.';
  }
  async function load() {
    invalidate();
    if (page.hidden || pending) return;
    if (page.dataset.packageReady === 'false') {
      status.textContent='Load the selected package before reading its tracing settings. Model discovery does not require this read.';
      return;
    }
    const revision=epoch, ticket={}; pending=ticket; controller=new AbortController();
    const current=()=>revision===epoch && context===selection();
    button.disabled=true; status.textContent='Checking server tracing configuration, without sending a model request…';
    const [project,pkg]=JSON.parse(context);
    const headers={Accept:'application/json'};
    if(project) headers['X-Workbench-Project']=project;
    if(project&&pkg) headers['X-Workbench-Package']=pkg;
    try {
      const response=await fetch('/api/tracing/status',{headers,signal:controller.signal,credentials:'same-origin',cache:'no-store'});
      if(!current()) return;
      if(response.status===404) {
        status.textContent='Tracing configuration endpoint unavailable (HTTP 404). This server does not expose /api/tracing/status. Tracing state is unknown, not confirmed off. Refresh after the server configuration endpoint is available.';
        document.dispatchEvent(new CustomEvent('orion:tracing-status',{detail:{state:'unknown',reason:'configuration_endpoint_unavailable'}}));
        return;
      }
      if(!response.ok) throw new Error(`HTTP ${response.status}: configuration not confirmed.`);
      const data=await response.json();
      if(!current()) return;
      const reasons={not_opted_in:'Server opt-in is off.',server_key_required:'A server-side LangSmith secret must be configured.',explicit_project_required:'An explicit LangSmith project is required.',endpoint_not_approved:'The configured trace destination is not approved.',not_probed:'Configured; no live delivery has been verified.'};
      const state = ['server_key_required','explicit_project_required'].includes(data.reason) ? 'Not configured' : String(data.state || 'unknown').replaceAll('_',' ');
      status.textContent=`LangSmith: ${state}. ${reasons[data.reason]??'Review server configuration.'}${data.project?` Project: ${data.project}.`:''} No credentials are requested or stored by this UI.`;
      document.dispatchEvent(new CustomEvent('orion:tracing-status',{detail:data}));
    } catch(error) {if(current()) status.textContent=`Tracing configuration unavailable. ${error.message}`;}
    finally {if(pending===ticket) {pending=null; controller=null; button.disabled=false;}}
  }
  button.addEventListener('click',load);
  document.addEventListener('change',event=>{if(['project-select','project-package','estate-package-select'].includes(event.target.id)) {invalidate(); if(!page.hidden) load();}});
  new MutationObserver(()=>{if(!page.hidden) load();}).observe(page,{attributes:true,attributeFilter:['hidden','data-package-ready']});
  load();
})();