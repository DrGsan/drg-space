export function moduleEnabled(state, id) {
  const spec=state.moduleCatalog?.find(module=>module.id===id);
  if(spec?.kind==='widget')return spec.requires?moduleEnabled(state,spec.requires):true;
  return !!spec && (state.me.settings.modules?.[id] ?? spec.default_enabled);
}
