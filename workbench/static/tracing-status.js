/* Configuration read only. Never asks the browser for tracing credentials. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const page = document.getElementById('page-agents');
  if (!page) return;
  const element = (tag, text, className = '') => {const e=document.createElement(tag); e.textContent=text; e.className=className; return e;};
  const panel=element('section',''); panel.className='agent-observability'; panel.id='agent-observability';
  const header=element('div',''); header.className='agent-observability-header';
  header.append(element('span','AGENT PLATFORM','agent-observability-eyebrow'),
    element('h3','Model access & run tracing'),
    element('p','Inference governance and execution traces are separate. These checks read configuration only and do not send a model request.'));
  const refresh=element('button','Refresh status'); refresh.type='button'; refresh.className='secondary compact';
  header.append(refresh); panel.append(header);
  const cards=element('div',''); cards.className='agent-observability-grid';
  const routeCard=element('section',''); routeCard.className='agent-observability-card';
  routeCard.append(element('span','INFERENCE','agent-observability-eyebrow'),element('h4','Model route'));
  const routeBadge=element('span','Route not checked','agent-observability-badge is-unknown');
  const routeName=element('strong','No saved model route confirmed.','agent-observability-route');
  const routeNote=element('p','A saved route is configuration only. This status check does not test inference or tool behavior.','agent-observability-note');
  const gatewayLine=element('p','Unity Catalog AI Gateway status is unknown.','agent-observability-detail');
  const modelSettings=element('a','Open model settings'); modelSettings.href='#workspace-model-panel'; modelSettings.className='agent-observability-link';
  routeCard.append(routeBadge,routeName,routeNote,gatewayLine,modelSettings);
  const traceCard=element('section',''); traceCard.className='agent-observability-card';
  traceCard.append(element('span','OBSERVABILITY','agent-observability-eyebrow'),element('h4','Run traces'));
  const mlflowBadge=element('span','MLflow not integrated','agent-observability-badge is-unavailable');
  const mlflowNote=element('p','No MLflow experiment or trace is connected to this app yet. A trace link will appear here only after a real trace is recorded.','agent-observability-note');
  const traceLink=element('button','Open MLflow trace'); traceLink.type='button'; traceLink.disabled=true; traceLink.className='secondary compact agent-observability-trace-link';
  const legacy=element('details',''); legacy.className='agent-observability-legacy';
  const legacySummary=element('summary','Legacy LangSmith status');
  const tracingStatus=element('p','Configuration not checked.'); tracingStatus.setAttribute('role','status'); tracingStatus.className='agent-observability-detail';
  legacy.append(legacySummary,tracingStatus);
  traceCard.append(mlflowBadge,mlflowNote,traceLink,legacy);
  cards.append(routeCard,traceCard); panel.append(cards);
  const privacy=element('p','Trace payload policy: prompts, XML, SQL, documents, and tool arguments/results stay out of this status view. No independent quality score or migration-parity claim is implied.','agent-observability-privacy');
  panel.append(privacy);
  page.querySelector('#agent-availability').after(panel);
  const selection=()=>JSON.stringify([document.getElementById('project-select').value,
    document.getElementById('estate-package-select')?.value || document.getElementById('project-package').value]);
  let epoch=0, context=selection(), pending=null, controller=null;
  function invalidate() {
    const next=selection();
    if(next===context) return;
    context=next; epoch+=1; controller?.abort(); pending=null; refresh.disabled=false;
    routeBadge.textContent='Selection changed'; routeBadge.className='agent-observability-badge is-unknown';
    routeName.textContent='No saved route confirmed for the new selection.';
    gatewayLine.textContent='Unity Catalog AI Gateway status is unknown until configuration is read.';
    tracingStatus.textContent='Selection changed. Trace configuration is unknown until read.';
  }
  function renderRoute(data) {
    const rawRoutes=data?.model_routes && typeof data.model_routes==='object' ? Object.values(data.model_routes) : [];
    const endpoints=[...new Set(rawRoutes.map(route=>typeof route==='string'?route:route?.endpoint).filter(value=>typeof value==='string'&&value.trim()))];
    const endpoint=data?.active_endpoint || (endpoints.length===1?endpoints[0]:null);
    const external=data?.mode==='external';
    const configured=data?.inference_ready===true || (external&&data?.configured===true);
    routeBadge.textContent=configured?'Configured · not inference-tested':data?.connected?'Connected · not inference-tested':'No route confirmed';
    routeBadge.className=`agent-observability-badge ${configured?'is-configured':data?.connected?'is-configured':'is-unknown'}`;
    routeName.textContent=endpoint?`${external?'External model':'Workspace endpoint'} · ${endpoint}`:external?`External model · ${data.model||'model not reported'}`:'No model route returned by the service.';
    const qualified=typeof endpoint==='string'&&/^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$/.test(endpoint);
    gatewayLine.textContent=qualified?'A Unity Catalog service name is present in the saved route. Gateway inference is not verified by this read.':'Current workspace routes use Model Serving endpoint names; Unity Catalog AI Gateway is not configured in this route.';
    document.dispatchEvent(new CustomEvent('orion:model-route-status',{detail:{status:configured?'configured_not_tested':'unknown',mode:data?.mode||'unknown',endpoint:endpoint||null,unity_gateway_service:qualified}}));
  }
  function renderRouteError(error) {
    routeBadge.textContent='Route status unavailable'; routeBadge.className='agent-observability-badge is-unknown';
    routeName.textContent='The saved model route could not be confirmed.';
    gatewayLine.textContent=`Unity Catalog AI Gateway status is unknown. ${error?.message||''}`.trim();
    document.dispatchEvent(new CustomEvent('orion:model-route-status',{detail:{status:'unknown',reason:'route_status_unavailable'}}));
  }
  function renderTracing(data) {
    const state=['server_key_required','explicit_project_required'].includes(data?.reason)?'Not configured':String(data?.state||'unknown').replaceAll('_',' ');
    const reasons={not_opted_in:'Server opt-in is off.',server_key_required:'A server-side LangSmith secret must be configured.',explicit_project_required:'An explicit LangSmith project is required.',endpoint_not_approved:'The configured trace destination is not approved.',not_probed:'Configured, but delivery has not been verified.'};
    tracingStatus.textContent=`LangSmith: ${state}. ${reasons[data?.reason]||'Review server configuration.'}${data?.project?` Project: ${data.project}.`:''} No credentials are requested or stored by this UI.`;
    document.dispatchEvent(new CustomEvent('orion:tracing-status',{detail:data}));
  }
  async function load() {
    invalidate();
    if (page.hidden || pending) return;
    if (page.dataset.packageReady === 'false') {
      routeBadge.textContent='Select a ready package'; routeBadge.className='agent-observability-badge is-unknown';
      routeName.textContent='No model route is shown until a ready package is selected.';
      gatewayLine.textContent='Unity Catalog AI Gateway status is not checked.';
      tracingStatus.textContent='Load the selected package to read the scoped trace configuration. Model discovery does not require this read.';
      return;
    }
    const revision=epoch, ticket={}; pending=ticket; controller=new AbortController();
    const current=()=>revision===epoch && context===selection();
    refresh.disabled=true; routeBadge.textContent='Reading saved configuration'; routeBadge.className='agent-observability-badge is-pending';
    tracingStatus.textContent='Reading server trace configuration. No model request is sent.';
    const [project,pkg]=JSON.parse(context);
    const headers={Accept:'application/json'};
    if(project) headers['X-Workbench-Project']=project;
    if(project&&pkg) headers['X-Workbench-Package']=pkg;
    try {
      const request=async path=>{const response=await fetch(path,{headers,signal:controller.signal,credentials:'same-origin',cache:'no-store'});if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.json();};
      const [model,tracing]=await Promise.allSettled([request('/api/models/status'),request('/api/tracing/status')]);
      if(!current()) return;
      if(model.status==='fulfilled') renderRoute(model.value); else renderRouteError(model.reason);
      if(tracing.status==='fulfilled') renderTracing(tracing.value);
      else {tracingStatus.textContent=`Trace configuration unavailable. ${tracing.reason?.message||'Status was not returned.'}`;document.dispatchEvent(new CustomEvent('orion:tracing-status',{detail:{state:'unknown',reason:'configuration_unavailable'}}));}
    } catch(error) {if(current()){renderRouteError(error);tracingStatus.textContent=`Configuration unavailable. ${error.message}`;}}
    finally {if(pending===ticket) {pending=null; controller=null; refresh.disabled=false;}}
  }
  refresh.addEventListener('click',load);
  document.addEventListener('change',event=>{if(['project-select','project-package','estate-package-select'].includes(event.target.id)) {invalidate(); if(!page.hidden) load();}});
  new MutationObserver(()=>{if(!page.hidden) load();}).observe(page,{attributes:true,attributeFilter:['hidden','data-package-ready']});
  load();
})();