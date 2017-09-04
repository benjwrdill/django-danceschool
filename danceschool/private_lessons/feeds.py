from django.http import JsonResponse
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404

from datetime import datetime, timedelta
import pytz

from danceschool.core.models import Instructor
from danceschool.core.utils.timezone import ensure_timezone

from .models import InstructorAvailabilitySlot, PrivateLessonEvent


class AvailabilityFeedItem(object):

    def __init__(self,object,**kwargs):

        timeZone = pytz.timezone(getattr(settings,'TIME_ZONE','UTC'))
        if kwargs.get('timeZone',None):
            try:
                timeZone = pytz.timezone(kwargs.get('timeZone',None))
            except pytz.exceptions.UnknownTimeZoneError:
                pass

        self.id = 'instructorAvailability_' + str(object.id)
        self.type = 'instructorAvailability'
        self.id_number = object.id
        self.title = object.name
        self.start = timezone.localtime(object.startTime,timeZone) \
            if timezone.is_aware(object.startTime) else object.startTime
        self.end = self.start + timedelta(minutes=object.duration)
        self.availableDurations = object.availableDurations
        self.availableRoles = object.availableRoles
        self.pricingTier = object.pricingTier.name
        self.onlinePrice = object.pricingTier.onlinePrice
        self.doorPrice = object.pricingTier.doorPrice
        self.status = object.status
        self.className = ['availabilitySlot','availabilitySlot-%s' % object.status]

        if object.location:
            self.location = object.location.name + '\n' + object.location.address + '\n' + object.location.city + ', ' + object.location.state + ' ' + object.location.zip
            self.location_id = object.location.id
        else:
            self.location = None
            self.location_id = None


class PrivateLessonFeedItem(object):

    def __init__(self,object,**kwargs):

        timeZone = pytz.timezone(getattr(settings,'TIME_ZONE','UTC'))
        if kwargs.get('timeZone',None):
            try:
                timeZone = pytz.timezone(kwargs.get('timeZone',None))
            except pytz.exceptions.UnknownTimeZoneError:
                pass

        self.id = 'privatelesson_' + str(object.event.id) + '_' + str(object.id)
        self.type = 'privatelesson'
        self.id_number = object.event.id
        self.title = object.event.name
        self.start = timezone.localtime(object.startTime,timeZone) \
            if timezone.is_aware(object.startTime) else object.startTime
        self.end = timezone.localtime(object.endTime,timeZone) \
            if timezone.is_aware(object.endTime) else object.endTime
        if hasattr(object,'event.location'):
            self.location = object.event.location.name + '\n' + object.event.location.address + '\n' + object.event.location.city + ', ' + object.event.location.state + ' ' + object.event.location.zip
        else:
            self.location = None


# This function creates a JSON feed of all available private lesson
# slots so that lessons may be booked using JQuery fullcalendar.
def json_availability_feed(request,instructor_id=None):
    if not instructor_id:
        return JsonResponse({})

    startDate = request.GET.get('start','')
    endDate = request.GET.get('end','')
    timeZone = request.GET.get('timezone',getattr(settings,'TIME_ZONE','UTC'))

    time_filter_dict_events = {}
    if startDate:
        time_filter_dict_events['startTime__gte'] = ensure_timezone(datetime.strptime(startDate,'%Y-%m-%d'))
    if endDate:
        time_filter_dict_events['startTime__lte'] = ensure_timezone(datetime.strptime(endDate,'%Y-%m-%d')) + timedelta(days=1)

    this_instructor = Instructor.objects.get(id=instructor_id)

    availability = InstructorAvailabilitySlot.objects.filter(
        instructor=this_instructor,
    ).filter(**time_filter_dict_events)

    if hasattr(request.user,'staffmember') and request.user.staffmember == this_instructor:
        eventlist = [AvailabilityFeedItem(x,timeZone=timeZone).__dict__ for x in availability]
    else:
        eventlist = [AvailabilityFeedItem(x,timeZone=timeZone).__dict__ for x in availability if x.isAvailable]

    return JsonResponse(eventlist,safe=False)


# This function creates a JSON feed of all scheduled private lessons
# so that instructors can see their own personal calendar of upcoming events.
def json_scheduled_feed(request,instructorFeedKey=''):
    if not instructorFeedKey:
        return JsonResponse({})

    try:
        this_instructor = Instructor.objects.get(feedKey=instructorFeedKey)
    except ObjectDoesNotExist:
        return Http404()

    startDate = request.GET.get('start','')
    endDate = request.GET.get('end','')
    timeZone = request.GET.get('timezone',getattr(settings,'TIME_ZONE','UTC'))

    time_filter_dict_events = {}
    if startDate:
        time_filter_dict_events['startTime__gte'] = ensure_timezone(datetime.strptime(startDate,'%Y-%m-%d'))
    if endDate:
        time_filter_dict_events['startTime__lte'] = ensure_timezone(datetime.strptime(endDate,'%Y-%m-%d')) + timedelta(days=1)

    lessons = PrivateLessonEvent.objects.filter(
        eventstaffmember__staffMember=this_instructor,
    ).filter(**time_filter_dict_events)

    eventlist = [PrivateLessonFeedItem(x,timeZone=timeZone).__dict__ for x in lessons]
    return JsonResponse(eventlist,safe=False)
