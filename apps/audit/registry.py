from django.db.models.signals import post_delete, post_save, pre_save


def _norm(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def audited(model=None, *,
            exclude=("created_at", "updated_at", "created_by", "updated_by"),
            mask=(), subject=None, company=None, module=None):
    """
    exclude: field names not worth auditing
    mask:    fields recorded as changed, but with values hidden (salary, password)
    subject: attribute holding the affected employee's id ("pk" on Employee itself,
             "employee_id" on child tables). Powers 'everything about this person'.
    company: attribute holding the company id ("company_id"). Powers per-company filtering.
    """
    def wrap(cls):
        _register(cls, set(exclude), set(mask), subject, company, module)
        return cls
    return wrap(model) if model is not None else wrap


def _register(model, exclude, mask, subject, company, module):
    fields = [f for f in model._meta.concrete_fields
              if f.name not in exclude and not f.primary_key]
    attnames = [f.attname for f in fields]
    label = model._meta.label

    def entry(field, old, new, include_old=True, include_new=True):
        if field.name in mask:
            return {"masked": True}
        out = {}
        if include_old:
            out["old"] = _norm(old)
        if include_new:
            out["new"] = _norm(new)
        return out

    def subject_id(instance):
        return getattr(instance, subject, None) if subject else None

    def company_id_of(instance):
        return getattr(instance, company, None) if company else None

    def emit(action, instance, changes):
        from .services import log   # lazy: avoids import cycles at startup
        log(action, instance, module=module or model._meta.app_label,
            subject_employee_id=subject_id(instance),
            company_id=company_id_of(instance),
            changes=changes)

    def on_pre_save(sender, instance, raw=False, **kwargs):
        instance._audit_old = None
        if raw or instance._state.adding or instance.pk is None:
            return
        instance._audit_old = (
            sender._base_manager.filter(pk=instance.pk).values(*attnames).first()
        )

    def on_post_save(sender, instance, created, raw=False, **kwargs):
        if raw:
            return
        if created:
            changes = {f.name: entry(f, None, getattr(instance, f.attname), include_old=False)
                       for f in fields}
            emit("create", instance, changes)
            return
        old = getattr(instance, "_audit_old", None) or {}
        changes = {}
        for f in fields:
            before, after = _norm(old.get(f.attname)), _norm(getattr(instance, f.attname))
            if before != after:
                changes[f.name] = entry(f, before, after)
        if changes:
            emit("update", instance, changes)

    def on_post_delete(sender, instance, **kwargs):
        changes = {f.name: entry(f, getattr(instance, f.attname), None, include_new=False)
                   for f in fields}
        emit("delete", instance, changes)

    post_save.connect(on_post_save, sender=model, weak=False, dispatch_uid=f"audit:{label}:post_save")
    pre_save.connect(on_pre_save, sender=model, weak=False, dispatch_uid=f"audit:{label}:pre_save")
    post_delete.connect(on_post_delete, sender=model, weak=False, dispatch_uid=f"audit:{label}:post_delete")