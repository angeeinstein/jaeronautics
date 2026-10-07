"""The form rules, for the API: a JSON body checked by the same WTForms form
the pages used, so what may be typed is written down once (forms.py) --
member categories, university addresses and year groups included -- and the
signup in step 7 checks exactly what My Account checks.
"""

from werkzeug.datastructures import MultiDict

from ..services import ValidationError


def checked(form_class, data, **attributes):
    """The values after the form's rules (trimmed, upper-cased year group, an
    unasked field emptied), or a ValidationError naming each field's problem.

    ``attributes`` are set on the form before it checks, such as
    ``member_category_value`` for a form that has no category of its own.
    """
    form = form_class(
        formdata=MultiDict({name: "" if value is None else value for name, value in data.items()}),
        meta={"csrf": False},
    )
    for name, value in attributes.items():
        setattr(form, name, value)
    if not form.validate():
        raise ValidationError(
            "Please check the form.",
            details={"fields": {name: str(errors[0]) for name, errors in form.errors.items() if errors}},
        )
    return {name: form[name].data for name in data if name in form}
