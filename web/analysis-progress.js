'use strict';

// A list-local presentation of the existing task. The owner supplies task
// updates and controls; this module never starts a second polling/model loop.
window.PanxuAnalysisProgress = (() => {
  const mounts = new Map();
  const number = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const node = (tag, cls = '', text) => {const value = document.createElement(tag); value.className = cls; if(text !== undefined)value.textContent = String(text); return value;};
  const add = (host, ...children) => {children.filter(Boolean).forEach(child => host.append(child)); return host;};
  const write = (target, text) => {if(target.textContent !== text)target.textContent = text;};
  const labels = {ready:'准备分析',collecting:'正在检查',analyzing:'正在分析',finalizing:'正在整理建议',paused:'分析已暂停',waiting:'等待补充信息',completed:'分析已结束',limited:'连续分析已暂停',error:'分析暂未完成'};
  const scoreLabels = {1:'建议保留',2:'倾向保留',3:'待你决定',4:'倾向清理',5:'建议清理'};
  function phase(task) {
    if(labels[task?.analysis_progress?.phase])return task.analysis_progress.phase;
    return ({investigating:'analyzing',paused:'paused',needs_input:'waiting',needs_model:'waiting',budget_exhausted:'limited',error:'error',ready:'ready',completed:'completed',awaiting_review:'completed'})[task?.status] || 'waiting';
  }
  function statistics(task) {
    const supplied = task?.analysis_progress || {}, rows = [...new Map((task?.advice || []).filter(item => item?.id && !item.stale).map(item => [item.id,item])).values()];
    const counts = {}, saved = number(supplied.saved_count) ?? number(task?.advice_total) ?? rows.length;
    let completeCounts = true;
    for(let score = 1; score <= 5; score++) {
      const count = number(supplied.rating_counts?.[score]);
      if(count === null)completeCounts = false;
      counts[score] = count ?? rows.filter(item => (item.cleanup_rating ?? item.rating) === score).length;
    }
    const rated = number(supplied.rated_count) ?? Object.values(counts).reduce((sum,value) => sum + value,0);
    const target = number(supplied.target_count) ?? (Object.hasOwn(supplied,'target_count') ? null : number(task?.analysis?.target_count));
    const remaining = number(supplied.remaining_count), covered = target !== null && remaining !== null && remaining <= target ? target - remaining : null;
    return {saved, rated, target, counts, covered, skipped:number(supplied.skipped_count), noAdvice:number(supplied.no_advice_count),
      remaining, stale:number(supplied.stale_count) || 0, partial:!completeCounts && saved > rows.length};
  }
  function unmount(host) {
    for(const [key,value] of [...mounts])if(!host || key === host)value.unmount();
  }
  function sessionResources(task) {
    const session=task?.analysis_session || task?.analysis_progress?.session;
    if(!session || typeof session!=='object')return null;
    const used=session.used || {},limits=session.limits || {},reported=number(used.reported_usage_calls);
    const resources=[['model_requests','模型请求','次'],['tool_calls','工具调用','次']].map(([key,label,unit])=>({label,unit,used:number(used[key]),limit:number(limits[key])}));
    if(number(used.elapsed_ms)!==null&&number(limits.elapsed_minutes)!==null)resources.push({label:'分析用时',unit:'分钟',used:used.elapsed_ms/60000,limit:limits.elapsed_minutes});
    if(reported>0&&number(used.input_tokens)!==null&&number(used.output_tokens)!==null)resources.push({label:'已报告 token',unit:'token',used:used.input_tokens+used.output_tokens,limit:number(limits.total_tokens)});
    return {session,resources:resources.filter(value=>value.used!==null&&value.limit>0).sort((a,b)=>b.used/b.limit-a.used/a.limit)};
  }
  function limitMessage(task) {
    const session=sessionResources(task)?.session,reason=session?.stop_reason;
    if(reason==='no_progress')return '连续检查没有取得新信息，已暂停以避免重复消耗。已有结果保留，可调整要求后继续。';
    if(reason==='repeated_failures')return '连续请求未能完成，已暂停。已有结果保留，请检查连接或补充要求后重试。';
    if(session)return '已达到本次连续分析的资源额度。已有结果保留，继续分析会开始新的额度。';
    return '本轮资源已用完，已有建议保留；继续分析不会丢失进度。';
  }
  function mount(host, helpers = {}) {
    unmount(host);
    const section = node('section','analysis-progress'), header = node('div','analysis-progress-header'), identity = node('div','analysis-progress-identity');
    const dot = node('span','analysis-progress-indicator'), title = node('h2','analysis-progress-title'), count = node('p','analysis-progress-count');
    const actions = node('div','analysis-progress-actions'), control = node('button','button small'), question = node('button','plain-button');
    control.type = question.type = 'button'; question.textContent = '回答问题';
    add(identity,dot,title); add(header,identity,count,actions); add(actions,control,question);
    const progress = node('progress','analysis-progress-meter'), note = node('p','analysis-progress-note');
    progress.setAttribute('aria-label','本次分析进度'); note.setAttribute('role','status'); note.setAttribute('aria-live','polite');
    const distribution = node('div','analysis-progress-distribution'), scoreBar = node('div','analysis-progress-scorebar'), scoreLegend = node('div','analysis-progress-scorelegend');
    scoreBar.setAttribute('role','img'); add(distribution,scoreBar,scoreLegend);
    const scoreNodes = [];
    for(let score = 1; score <= 5; score++) {
      const segment = node('span','analysis-progress-score'), legend = node('span','analysis-progress-score-label');
      segment.dataset.rating = legend.dataset.rating = String(score); scoreBar.append(segment); scoreLegend.append(legend); scoreNodes.push({score,segment,legend});
    }
    const budget = node('div','analysis-progress-budget'), budgetLabel = node('span'), budgetMeter = node('progress','analysis-progress-budget-meter');
    budgetMeter.setAttribute('aria-label','本轮模型请求额度'); add(budget,budgetLabel,budgetMeter);
    const error = node('p','field-error analysis-progress-error'); error.setAttribute('role','alert');
    add(section,header,progress,note,distribution,budget,error); host.append(section);
    const token = {host, section, task:null, active:true, pending:'', pauseRequested:false, error:'', off:null};
    const accepts = task => (!helpers.taskId || task?.id === helpers.taskId) && (!helpers.acceptTask || helpers.acceptTask(task));
    function paint() {
      const task = token.task; section.hidden = !task || task.task_kind === 'operation' || task.status === 'executing';
      if(section.hidden)return;
      const stats = statistics(task), state = phase(task), busy = task.status === 'investigating', stopping = busy && (token.pending === 'pause' || token.pauseRequested);
      section.dataset.phase = state; section.dataset.busy = String(busy); section.setAttribute('aria-label','列表分析进度');
      write(title,stopping ? '正在暂停分析' : task.status === 'needs_model' ? '需要连接模型' : labels[state]);
      write(count,`已保存 ${stats.saved} 项建议${stats.target !== null ? ` · ${stats.target} 项目标` : ''}`);
      const determinate = stats.target !== null && stats.target > 0 && stats.covered !== null;
      progress.hidden = !determinate && (!busy || stats.target === 0);
      if(determinate) {progress.max = stats.target; progress.value = stats.covered; progress.setAttribute('aria-valuetext',`已有结论 ${stats.covered} 项目标，共 ${stats.target} 项明确目标`);}
      else {progress.removeAttribute('value'); progress.removeAttribute('aria-valuetext'); progress.max = 1;}
      let text = stopping ? '正在停止后续请求；已接收的结果会保留。' : busy ? (stats.target === null ? '按需检查中，已有建议会逐项出现在下方。' : '已有建议会逐项出现在下方，可以先查看。') :
        state === 'limited' ? limitMessage(task) :
        state === 'paused' ? '已有建议与进度已保留，可以继续分析。' :
        task.question ? '回答后会继续分析；已有建议仍可查看。' :
        state === 'error' ? '已有建议已保留，可以重试或检查模型连接。' :
        state === 'completed' ? '按评分筛选、勾选，再确认要执行的操作。' : '已有进度已保留。';
      const outcomes = [stats.skipped ? `跳过 ${stats.skipped} 项` : '', stats.noAdvice ? `${stats.noAdvice} 项暂无法建议` : '', stats.stale ? `${stats.stale} 条建议执行前需刷新检查` : ''].filter(Boolean);
      if(stats.remaining > 0)text += ` 还有 ${stats.remaining} 项目标待分析。`;
      if(outcomes.length)text += ' ' + outcomes.join(' · ') + '。';
      write(note,text);
      note.hidden=state==='completed'&&stats.remaining===0&&!outcomes.length;
      question.hidden = !(task.question || task.status === 'needs_input') || typeof helpers.onQuestion !== 'function'; question.disabled = Boolean(token.pending); write(question,task.question ? '回答问题' : '补充要求');
      const canPause = busy && typeof helpers.onPause === 'function', canContinue = !task.question && ['paused','budget_exhausted','error','needs_model','ready'].includes(task.status) && typeof helpers.onContinue === 'function';
      const canSettings = task.status === 'needs_model' && typeof helpers.onSettings === 'function';
      control.hidden = !canPause && !canContinue && !canSettings;
      write(control,stopping ? '暂停中…' : token.pending === 'continue' ? '正在继续…' : canPause ? '暂停分析' : canSettings ? '连接模型' : task.status === 'error' ? '重试分析' : task.status === 'ready' ? '开始分析' : '继续分析');
      control.disabled = Boolean(token.pending) || stopping || canPause && !task.job_id;
      control.dataset.action = canPause ? 'pause' : canSettings ? 'settings' : 'continue';
      distribution.hidden = helpers.showRatingDistribution !== true || stats.rated === 0;
      const names = [];
      for(const {score,segment,legend} of scoreNodes) {
        const n = stats.counts[score]; segment.style.flexGrow = String(n); segment.hidden = legend.hidden = n === 0;
        write(legend,`${scoreLabels[score]} ${n}`); names.push(`${scoreLabels[score]} ${n} 项`);
      }
      scoreBar.setAttribute('aria-label',(stats.partial ? '已显示建议的评分分布：' : '评分分布：') + names.join('，'));
      const session=sessionResources(task),availableBudgets = session?.resources || [['model_requests','模型请求'],['tool_calls','工具调用']].map(([key,label]) => {
        const value = task.analysis_progress?.budget?.[key]; return {label,unit:'次',used:number(value?.used),limit:number(value?.limit)};
      }).filter(value => value.used !== null && value.limit !== null && value.limit > 0).sort((a,b) => b.used / b.limit - a.used / a.limit), resource = availableBudgets[0];
      budget.hidden = state==='completed' || !resource || state !== 'limited' && resource.used / resource.limit < .75;
      if(!budget.hidden) {const scope=session?'本次连续分析':'本轮',amount=resource.unit==='分钟'?resource.used.toFixed(1):resource.used.toLocaleString('zh-CN');write(budgetLabel,`${scope} · ${resource.label} ${amount} / ${resource.limit.toLocaleString('zh-CN')} ${resource.unit}`); budgetMeter.max = resource.limit; budgetMeter.value = Math.min(resource.used,resource.limit); budgetMeter.setAttribute('aria-label',scope + resource.label + '额度'); budgetMeter.setAttribute('aria-valuetext',`${amount} ${resource.unit}，额度 ${resource.limit} ${resource.unit}；不是项目数量`);}
      write(error,token.error); error.hidden = !token.error;
    }
    function update(task) {
      if(!token.active || task && !accepts(task))return;
      if(token.task?.id !== task?.id) {token.pending = ''; token.pauseRequested = false; token.error = '';}
      token.task = task || null;
      if(task?.status !== 'investigating')token.pauseRequested = false;
      paint();
    }
    async function run(kind) {
      if(!token.active || token.pending || !token.task)return;
      const task = token.task, callback = ({pause:helpers.onPause,continue:helpers.onContinue,settings:helpers.onSettings,question:helpers.onQuestion})[kind];
      if(typeof callback !== 'function')return;
      token.pending = kind; token.error = ''; paint();
      try {
        const response = await callback(task);
        if(!token.active || token.task?.id !== task.id)return;
        const result = response?.task || response;
        if(result?.id === task.id)update(result);
        if(kind === 'pause' && token.task.status === 'investigating')token.pauseRequested = true;
      } catch(problem) {if(token.active && token.task?.id === task.id)token.error = problem?.message || '操作暂未完成，请重试。';}
      finally {if(token.active && token.task?.id === task.id) {token.pending = ''; paint();}}
    }
    control.addEventListener('click',() => {if(!control.disabled)return run(control.dataset.action);});
    question.addEventListener('click',() => {if(!question.disabled)return run('question');});
    token.update = update; token.unmount = () => {if(!token.active)return; token.active = false; token.off?.(); section.remove(); if(mounts.get(host) === token)mounts.delete(host);};
    mounts.set(host,token); update(helpers.task || null);
    if(typeof helpers.subscribe === 'function')token.off = helpers.subscribe(update);
    return {update,unmount:token.unmount,element:section};
  }
  return {mount,unmount,statistics,sessionResources,limitMessage};
})();
