from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from planner.models import CalendarEvent, Company


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)
    business_name = forms.CharField(max_length=120, label="Business name")

    class Meta:
        model = User
        fields = ("username", "email")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            Company.objects.create(
                owner=user,
                name=self.cleaned_data["business_name"],
                city="Pune",
            )
        return user


class EventForm(forms.ModelForm):
    class Meta:
        model = CalendarEvent
        fields = ("name", "event_type", "start_date", "end_date", "notes")
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.TextInput(),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            raise forms.ValidationError("The end date has to be on or after the start date.")
        return cleaned


class UploadForm(forms.Form):
    sales_file = forms.FileField(label="Sales file")


class StockForm(forms.Form):
    on_hand = forms.IntegerField(min_value=0, label="Units on hand")
    lead_time_days = forms.IntegerField(min_value=1, max_value=90, label="Supplier lead time (days)")
