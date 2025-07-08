from django import forms
from django.core.exceptions import ValidationError
from .models import Issue, Comment, InvitedUser, RoomData, Inventory, ProductData
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Field, Div

from django.forms.widgets import FileInput
class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        # Handle multiple files from files.getlist
        if not data and initial:
            return initial
        if not data:
            if self.required:
                raise ValidationError(self.error_messages['required'], code='required')
            return []
        if not isinstance(data, list):
            data = [data]
        return data  # Return
class MultipleFileInput(forms.FileInput):
    def __init__(self, attrs=None):
        super().__init__(attrs)
        if attrs is None:
            attrs = {}
        attrs['multiple'] = True

    def value_from_datadict(self, data, files, name):
        return files.getlist(name)  # Always return a list

    def value_omitted_from_data(self, data, files, name):
        return not files.getlist(name)  # True if no files  
        
class IssueForm(forms.ModelForm):
    # initial_comment = forms.CharField(widget=forms.Textarea, required=True)
    images = MultipleFileField(
        widget=MultipleFileInput(attrs={'class': 'form-control d-none', 'accept': 'image/*'}),
        required=False,
        label="Attach Images (Max 4)"
    )
    video = forms.FileField(
        widget=forms.FileInput(attrs={'class': 'form-control d-none', 'accept': 'video/*'}),
        required=False
    )
    related_rooms = forms.ModelMultipleChoiceField(
        queryset=RoomData.objects.all(),
        widget=forms.SelectMultiple(attrs={'class': 'form-control select2-multiple'}),
        required=False,
        label="Related Rooms (if type is Room)"
    )
    related_floors = forms.MultipleChoiceField(
        widget=forms.SelectMultiple(attrs={'class': 'form-control select2-multiple'}),
        # queryset=RoomData.objects.values_list('floor', flat=True).distinct(),
        required=False,
        label="Related Floors (if type is Floor)",
        choices=[(i, f'Floor {i}') for i in RoomData.objects.values_list('floor', flat=True).distinct()]  # Assuming max 20 floors
    )
    related_product = forms.ModelMultipleChoiceField(
        queryset=ProductData.objects.all(),
        widget=forms.SelectMultiple(attrs={'class': 'form-control select2-multiple'}),
        required=False,
        label="Related Products (if type is Product)"
    )
    other_type_details = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        required=False,
        label="Other Details (if type is Other)"
    )

    class Meta:
        model = Issue
        fields = ['title', 'type', 'description', 'images', 'video', 'related_rooms', 'related_floors', 'related_product', 'other_type_details']
        widgets = {
            'type': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set type choices
        self.fields['type'].choices = [
            ('', 'Select type'),
            ('ROOM', 'Room Issue'),
            ('FLOOR', 'Floor Issue'),
            ('PRODUCT', 'Product Issue'),
            ('OTHER', 'Other Issue'),
        ]
        # Set default to empty if not already set
        if not self.initial.get('type'):
            self.initial['type'] = ''

        self.helper = FormHelper()
        self.helper.layout = Layout(
            Div(
                Field('type', css_class='form-control'),
                css_class='mb-3 my-custom-class',
                css_id='div_id_type',
            ),
            # other fields...
        )

        





    def clean_related_floors(self):
        floors = self.cleaned_data.get('related_floors', [])
        try:
            return [int(f) for f in floors if f]
        except ValueError:
            raise forms.ValidationError("Invalid floor selection.")

    def clean(self):
        cleaned_data = super().clean()
        issue_type = cleaned_data.get("type")
        related_rooms = cleaned_data.get("related_rooms")
        related_floors = cleaned_data.get("related_floors")
        related_product = cleaned_data.get("related_product")
        other_details = cleaned_data.get("other_type_details")

        if issue_type == "ROOM" and not related_rooms:
            self.add_error('related_rooms', "Please select at least one room for issues of type 'Room'.")
        elif issue_type == "FLOOR" and not related_floors:
            self.add_error('related_floors', "Please select at least one floor for issues of type 'Floor'.")
        elif issue_type == "PRODUCT" and not related_product:
            self.add_error('related_product', "Please select at least one product for issues of type 'Product'.")
        elif issue_type == "OTHER" and not other_details:
            self.add_error('other_type_details', "Please provide details for issues of type 'Other'.")

        # Clear irrelevant fields
        if issue_type != "ROOM":
            cleaned_data['related_rooms'] = RoomData.objects.none()
        if issue_type != "FLOOR":
            cleaned_data['related_floors'] = []
        if issue_type != "PRODUCT":
            cleaned_data['related_product'] = ProductData.objects.none()
        if issue_type != "OTHER":
            cleaned_data['other_type_details'] = None

        return cleaned_data

class CommentForm(forms.ModelForm):
    text_content = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 1, 'placeholder': 'Add your comment...', 'class': 'form-control'}),
        label='Your Comment',
        required=False
    )
    images = MultipleFileField(
        widget=MultipleFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        required=False,
        label="Attach Images (Max 4)"
    )
    video = forms.FileField(
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': 'video/*'}),
        required=False,
        label="Attach Video (Max 100MB)"
    )

    class Meta:
        model = Comment
        fields = ['text_content', 'images', 'video']

    def clean_images(self):
        images = self.cleaned_data.get('images', [])
        print(f"Images in clean_images: {images}")  # Debug
        if images:
            if len(images) > 4:
                raise ValidationError("You can upload a maximum of 4 images.")
            for image in images:
                if not image.content_type.startswith('image/'):
                    raise ValidationError(f"File '{image.name}' is not a valid image.")
                if image.size > 4 * 1024 * 1024:
                    raise ValidationError(f"Image '{image.name}' exceeds 4MB limit.")
        return images

    def clean_video(self):
        video = self.cleaned_data.get('video')
        if video:
            if not video.content_type.startswith('video/'):
                raise ValidationError("The uploaded file is not a valid video.")
            if video.size > 100 * 1024 * 1024:
                raise ValidationError("Video file size cannot exceed 100MB.")
        return video

    def clean(self):
        cleaned_data = super().clean()
        text_content = cleaned_data.get('text_content')
        images = cleaned_data.get('images', [])  # Use cleaned_data
        video = cleaned_data.get('video')
        if not text_content and not images and not video:
            raise ValidationError("A comment must contain text or at least one media file.")
        return cleaned_data

class IssueUpdateForm(forms.ModelForm):
    assignee = forms.ModelChoiceField(
        queryset=InvitedUser.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    observers = forms.ModelMultipleChoiceField(
        queryset=InvitedUser.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': '5'})
    )
    # Add fields from IssueForm
    images = MultipleFileField(
        widget=MultipleFileInput(attrs={'class': 'form-control d-none', 'accept': 'image/*'}),
        required=False,
        label="Attach Images (Max 4)"
    )
    video = forms.FileField(
        widget=forms.FileInput(attrs={'class': 'form-control d-none', 'accept': 'video/*'}),
        required=False
    )
    related_rooms = forms.ModelMultipleChoiceField(
        queryset=RoomData.objects.all(),
        widget=forms.SelectMultiple(attrs={'class': 'form-control select2-multiple'}),
        required=False,
        label="Related Rooms (if type is Room)"
    )
    related_floors = forms.MultipleChoiceField(
        widget=forms.SelectMultiple(attrs={'class': 'form-control select2-multiple'}),
        required=False,
        label="Related Floors (if type is Floor)",
        choices=[] # Will be populated in __init__
    )
    related_product = forms.ModelMultipleChoiceField(
        queryset=ProductData.objects.all(),
        widget=forms.SelectMultiple(attrs={'class': 'form-control select2-multiple'}),
        required=False,
        label="Related Products (if type is Product)"
    )
    other_type_details = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        required=False,
        label="Other Details (if type is Other)"
    )

    class Meta:
        model = Issue
        fields = ['title', 'description', 'status', 'type', 'is_for_hotel_owner', 'assignee', 'observers',
                  'images', 'video', 'related_rooms', 'related_floors', 'related_product', 'other_type_details'] # Added new fields
        widgets = {
            'description': forms.Textarea(attrs={'rows': 1}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'type': forms.Select(attrs={'class': 'form-select'}),
            'is_for_hotel_owner': forms.Select(choices=[
                (False, 'Hidden from Hotel Owner'),
                (True, 'Visible to Hotel Owner')
                ], attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure the creator is always included in the initial observers for existing issues
        if self.instance and self.instance.pk and hasattr(self.instance, 'created_by') and self.instance.created_by:
            creator_qs = InvitedUser.objects.filter(pk=self.instance.created_by.pk)
            current_initial_observers = self.initial.get('observers')

            if current_initial_observers:
                current_initial_observers_qs = InvitedUser.objects.none()
                if isinstance(current_initial_observers, list):
                    # current_initial_observers is a list. Its items might be PKs or InvitedUser instances.
                    processed_pks = []
                    for item in current_initial_observers:
                        if isinstance(item, InvitedUser): # Check if item is an InvitedUser instance
                            processed_pks.append(item.pk)
                        elif item is not None and str(item).strip() != '' : # Assume it's a PK (or something convertible to PK)
                            processed_pks.append(item)
                    
                    # valid_pks should now only contain actual primary key values
                    valid_pks = [pk for pk in processed_pks if pk is not None] # Redundant if above elif is strict, but safe

                    if valid_pks:
                        current_initial_observers_qs = InvitedUser.objects.filter(pk__in=valid_pks)
                elif hasattr(current_initial_observers, 'all'): # if already a queryset
                    current_initial_observers_qs = current_initial_observers
                
                self.initial['observers'] = current_initial_observers_qs.union(creator_qs)
            else:
                self.initial['observers'] = creator_qs

        # Set type choices (same as IssueForm)
        self.fields['type'].choices = [
            ('', 'Select type'),
            ('ROOM', 'Room Issue'),
            ('FLOOR', 'Floor Issue'),
            ('PRODUCT', 'Product Issue'),
            ('OTHER', 'Other Issue'),
        ]
        # Set related_floors choices (same as IssueForm)
        self.fields['related_floors'].choices = [(i, f'Floor {i}') for i in RoomData.objects.values_list('floor', flat=True).distinct()]


    def clean_observers(self):
        observers = self.cleaned_data.get('observers')
        if observers:
            # Ensure uniqueness in the cleaned data
            unique_observers = list(dict.fromkeys(observers))
            return unique_observers
        return observers

    # Copied from IssueForm
    def clean_related_floors(self):
        floors = self.cleaned_data.get('related_floors', [])
        try:
            return [int(f) for f in floors if f]
        except ValueError:
            raise forms.ValidationError("Invalid floor selection.")

    def clean(self):
        cleaned_data = super().clean()
        
        assignee = cleaned_data.get('assignee')
        # observers from cleaned_data is a list of instances (from clean_observers) or None
        submitted_observers_list = cleaned_data.get('observers') 

        # Convert list of submitted instances to a QuerySet
        submitted_observers_qs = InvitedUser.objects.none()
        if submitted_observers_list:
            observer_pks = [obs.pk for obs in submitted_observers_list if hasattr(obs, 'pk')]
            if observer_pks:
                submitted_observers_qs = InvitedUser.objects.filter(pk__in=observer_pks)

        # Initialize final_observers_qs with what was submitted via the form/modal
        final_observers_qs = submitted_observers_qs

        # If editing an existing issue AND the 'observers' field was NOT part of the POST data 
        # (i.e., user didn't interact with the observer modal to make an explicit selection),
        # then we ensure existing observers from the instance are preserved.
        if self.instance and self.instance.pk and 'observers' not in self.data: # self.data is request.POST
            final_observers_qs = final_observers_qs | self.instance.observers.all()
        
        # Add assignee to the set of observers
        if assignee:
            final_observers_qs = final_observers_qs | InvitedUser.objects.filter(pk=assignee.pk)
        
        # Add creator to the set of observers (for existing issues, to ensure they are always included)
        if self.instance and self.instance.pk and hasattr(self.instance, 'created_by') and self.instance.created_by:
            final_observers_qs = final_observers_qs | InvitedUser.objects.filter(pk=self.instance.created_by.pk)
        
        cleaned_data['observers'] = final_observers_qs.distinct() # Ensure uniqueness

        # --- Conditional validation for related fields based on type (existing logic) ---
        issue_type = cleaned_data.get("type")
        related_rooms = cleaned_data.get("related_rooms")
        related_floors = cleaned_data.get("related_floors")
        related_product = cleaned_data.get("related_product")
        other_details = cleaned_data.get("other_type_details")

        if issue_type == "ROOM" and not related_rooms:
            self.add_error('related_rooms', "Please select at least one room for issues of type 'Room'.")
        elif issue_type == "FLOOR" and not related_floors:
            self.add_error('related_floors', "Please select at least one floor for issues of type 'Floor'.")
        elif issue_type == "PRODUCT" and not related_product:
            self.add_error('related_product', "Please select at least one product for issues of type 'Product'.")
        elif issue_type == "OTHER" and not other_details:
            self.add_error('other_type_details', "Please provide details for issues of type 'Other'.")

        # Clear irrelevant fields based on type - important for updates
        if issue_type != "ROOM":
            cleaned_data['related_rooms'] = RoomData.objects.none()
        if issue_type != "FLOOR":
            cleaned_data['related_floors'] = []
        if issue_type != "PRODUCT":
            cleaned_data['related_product'] = ProductData.objects.none()
        if issue_type != "OTHER":
            cleaned_data['other_type_details'] = '' # Set to empty string instead of None for CharField

        return cleaned_data

    # Removed restrictive clean_type method. Model's type field choices will apply.
    # def clean_type(self):
    #     type = self.cleaned_data.get('type')
    #     if type not in ['ROOM', 'FLOOR']:
    #         raise ValidationError("Invalid type selected.")
    #     return type