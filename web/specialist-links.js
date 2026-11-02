/* A readable recommendation is never parsed into an executable command. */
window.PanxuSpecialistLinks = (() => {
  let opener = null;
  function reference(record = {}) {
    const assessment = record.assessment || record;
    if (assessment.stale || assessment.state === 'stale' || !record.id) return null;
    if (!assessment.specialist && !assessment.extra_suggestion_id && !record.specialist && !record.extra_suggestion_id) return null;
    return {item_id: record.id, signature: record.signature || assessment.signature,
      task_id: assessment.source_ref?.task_id || assessment.inherited_from?.task_id || assessment.task_id || assessment.source_task_id || record.task_id || undefined};
  }
  function create(record = {}) {
    const ref = reference(record);
    if (!ref) return null;
    const assessment = record.assessment || record;
    const extra = assessment.extra_suggestion_id || record.extra_suggestion_id;
    const entry = document.createElement('button');
    entry.type = 'button'; entry.className = 'plain-button specialist-entry';
    entry.textContent = extra ? '查看专项方案' : '打开专项工具';
    entry.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); opener?.(ref); });
    return entry;
  }
  return {reference, create, setOpener: value => { opener = typeof value === 'function' ? value : null; }};
})();
