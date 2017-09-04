from django.views.generic import FormView, TemplateView
from django.http import JsonResponse, HttpResponseRedirect, Http404
from django.contrib import messages
from django.core.exceptions import ValidationError, ObjectDoesNotExist
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from datetime import datetime, timedelta

from danceschool.core.models import Instructor, TemporaryRegistration, TemporaryEventRegistration, DanceRole, EventOccurrence, EventStaffMember
from danceschool.core.constants import getConstant, REG_VALIDATION_STR
from danceschool.core.utils.timezone import ensure_localtime

from .forms import SlotCreationForm, SlotUpdateForm, SlotBookingForm
from .models import InstructorAvailabilitySlot, PrivateLessonEvent


class InstructorAvailabilityView(TemplateView):
    template_name = 'private_lessons/instructor_availability_fullcalendar.html'

    def get(self,request,*args,**kwargs):
        # Only instructors or individuals with permission to change
        # other instructors' availability have permission to see this view.
        thisUser = getattr(request,'user',None)
        thisStaffMember = getattr(thisUser,'staffmember',None)
        if (
            (thisStaffMember and thisUser and thisUser.has_perm('private_lessons.edit_own_availability')) or
            (thisUser and thisUser.has_perm('private_lessons.edit_others_availability'))
        ):
            return super(InstructorAvailabilityView,self).get(request,*args,**kwargs)
        return Http404()

    def get_context_data(self,**kwargs):
        context = super(InstructorAvailabilityView,self).get_context_data(**kwargs)

        context.update({
            'instructor': getattr(self.request,'user').staffmember,
            'instructor_list': Instructor.objects.filter(
                availableForPrivates=True,instructorprivatelessondetails__isnull=False
            ),
            'creation_form': SlotCreationForm(),
            'update_form': SlotUpdateForm(),
        })

        return context


class AddAvailabilitySlotView(FormView):
    form_class = SlotCreationForm

    def get(self, request, *args, **kwargs):
        return JsonResponse({'valid': False})

    def form_invalid(self, form):
        return JsonResponse(form.errors, status=400)

    def form_valid(self, form):
        '''
        Create slots and return success message.
        '''
        startDate = form.cleaned_data['startDate']
        endDate = form.cleaned_data['endDate']
        startTime = form.cleaned_data['startTime']
        endTime = form.cleaned_data['endTime']
        instructor = form.cleaned_data['instructorId']

        interval_minutes = getConstant('privateLessons__lessonLengthInterval')

        this_date = startDate
        while this_date <= endDate:
            this_time = startTime
            while this_time < endTime:
                InstructorAvailabilitySlot.objects.create(
                    instructor=instructor,
                    startTime=ensure_localtime(datetime.combine(this_date, this_time)),
                    duration=interval_minutes,
                    location=form.cleaned_data.get('location'),
                    pricingTier=form.cleaned_data.get('pricingTier'),
                )
                this_time = (ensure_localtime(datetime.combine(this_date, this_time)) + timedelta(minutes=interval_minutes)).time()
            this_date += timedelta(days=1)

        return JsonResponse({'valid': True})


class UpdateAvailabilitySlotView(FormView):
    form_class = SlotUpdateForm
    http_method_names = ['post',]

    def get(self, request, *args, **kwargs):
        return JsonResponse({'valid': False})

    def form_invalid(self, form):
        return JsonResponse(form.errors, status=400)

    def form_valid(self, form):
        '''
        Modify or delete the availability slot as requested and return success message.
        '''
        slotIds = form.cleaned_data['slotIds']
        deleteSlot = form.cleaned_data.get('deleteSlot', False)

        these_slots = InstructorAvailabilitySlot.objects.filter(id__in=slotIds)

        if deleteSlot:
            these_slots.delete()
        else:
            for this_slot in these_slots:
                this_slot.location = form.cleaned_data['updateLocation']
                this_slot.status = form.cleaned_data['updateStatus']
                this_slot.pricingTier = form.cleaned_data.get('updatePricing')
                this_slot.save()

        return JsonResponse({'valid': True})


class BookPrivateLessonView(FormView):
    template_name = 'private_lessons/private_lesson_fullcalendar.html'
    form_class = SlotBookingForm

    def get_context_data(self,**kwargs):
        context = super(BookPrivateLessonView,self).get_context_data(**kwargs)
        context.update({
            'instructor_list': Instructor.objects.filter(
                availableForPrivates=True,instructorprivatelessondetails__isnull=False
            ),
        })
        return context

    def get_form_kwargs(self, **kwargs):
        '''
        Pass the current user to the form to render the payAtDoor field if applicable.
        '''
        kwargs = super(BookPrivateLessonView, self).get_form_kwargs(**kwargs)
        kwargs['user'] = self.request.user if hasattr(self.request,'user') else None
        return kwargs

    def form_valid(self, form):

        slotId = form.cleaned_data.pop('slotId')
        payAtDoor = form.cleaned_data.pop('payAtDoor',False)

        # Check that passed duration is valid.
        try:
            duration = int(form.cleaned_data.pop('duration'))
        except ValueError:
            form.add_error(None,ValidationError(_('Invalid duration.'),code='invalid'))
            return self.form_invalid(form)

        # Include the submission user if the user is authenticated
        if self.request.user.is_authenticated:
            submissionUser = self.request.user
        else:
            submissionUser = None

        try:
            thisSlot = InstructorAvailabilitySlot.objects.get(id=slotId)
        except ObjectDoesNotExist:
            form.add_error(None,ValidationError(_('Invalid slot ID.'),code='invalid'))
            return self.form_invalid(form)

        # Check that passed role is valid
        try:
            role = DanceRole.objects.filter(
                instructorprivatelessondetails__instructor=thisSlot.instructor,
            ).get(id=int(form.cleaned_data.pop('role')))
        except (ValueError, ObjectDoesNotExist):
            form.add_error(None,ValidationError(_('Invalid dance role.'),code='invalid'))
            return self.form_invalid(form)

        affectedSlots = InstructorAvailabilitySlot.objects.filter(
            instructor=thisSlot.instructor,
            location=thisSlot.location,
            pricingTier=thisSlot.pricingTier,
            startTime__gte=thisSlot.startTime,
            startTime__lte=thisSlot.startTime + timedelta(minutes=duration),
        ).filter(
            Q(status=InstructorAvailabilitySlot.SlotStatus.available) |
            (
                Q(status=InstructorAvailabilitySlot.SlotStatus.tentative) &
                ~Q(temporaryEventRegistration__registration__expirationDate__gte=timezone.now())
            )
        )

        # If someone cancels, there will already be one or more events associated
        # with these slots.  These need to be deleted, and we also validate to be
        # certain that we are not cancelling any Event which has finalized or in progress
        # registrations attached to it.
        existingEvents = PrivateLessonEvent.objects.filter(
            instructoravailabilityslot__id__in=[x.id for x in affectedSlots]
        ).distinct()

        if existingEvents.filter(
            Q(eventregistration__isnull=False) |
            Q(temporaryeventregistration__registration__expirationDate__gte=timezone.now())
        ).exists():
            form.add_error(None,ValidationError(_('Some or all of your requested lesson time is currently in the process of registration. Please select a new slot or try again later.'),code='invalid'))
            return self.form_invalid(form)
        else:
            existingEvents.delete()

        # Slots without pricing tiers can't go through the actual registration process.
        # Instead, they are simply booked and the user gets a success message.
        # TODO: Email confirmation for these individuals.
        if not thisSlot.pricingTier:
            affectedSlots.update(
                status=InstructorAvailabilitySlot.SlotStatus.booked,
            )
            messages.success(self.request,_('Your private lesson has been scheduled successfully.'))
            return HttpResponseRedirect(reverse('submissionRedirect'))

        regSession = self.request.session.get(REG_VALIDATION_STR, {})

        # The session expires after a period of inactivity that is specified in preferences.
        expiry = timezone.now() + timedelta(minutes=getConstant('registration__sessionExpiryMinutes'))
        self.request.session.set_expiry(60 * getConstant('registration__sessionExpiryMinutes'))

        lesson = PrivateLessonEvent.objects.create(
            pricingTier=thisSlot.pricingTier,
            participants=form.cleaned_data.pop('participants'),
            comments=form.cleaned_data.pop('comments'),
        )

        lesson_instructor = EventStaffMember.objects.create(
            event=lesson,
            category=getConstant('privateLessons__eventStaffCategoryPrivateLesson'),
            submissionUser=submissionUser,
        )

        lesson_occurrence = EventOccurrence.objects.create(
            event=lesson,
            startTime=thisSlot.startTime,
            endTime=thisSlot.startTime + timedelta(minutes=duration),
        )
        lesson_instructor.occurrences.add(lesson_occurrence)

        # Ensure that lesson start and end time are saved appropriately for
        # the event.
        lesson.save()

        # Create a Temporary Registration associated with this lesson.
        reg = TemporaryRegistration(
            submissionUser=submissionUser,dateTime=timezone.now(),
            payAtDoor=payAtDoor,
            expirationDate=expiry,
        )

        tr = TemporaryEventRegistration(
            event=lesson, role=role, price=lesson.getBasePrice(payAtDoor=payAtDoor)
        )

        # Any remaining form data goes into the JSONfield.
        reg.data = form.cleaned_data or {}

        # Now we are ready to save and proceed.
        reg.priceWithDiscount = tr.price
        reg.save()
        tr.registration = reg
        tr.save()

        affectedSlots.update(
            status=InstructorAvailabilitySlot.SlotStatus.tentative,
            temporaryEventRegistration=tr,
        )

        # Load the temporary registration into session data like a regular registration
        # and redirect to Step 2 as usual.

        regSession["temporaryRegistrationId"] = reg.id
        self.request.session[REG_VALIDATION_STR] = regSession
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse('getStudentInfo')
