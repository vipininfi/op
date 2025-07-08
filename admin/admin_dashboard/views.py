from django.shortcuts import render,HttpResponseRedirect, redirect, get_object_or_404
from django.contrib.auth import authenticate,login,logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from functools import wraps
from .models import *
from django.http import JsonResponse # Added this import

from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.urls import reverse # Though reverse might not be strictly needed if using namespaced redirects
import uuid
from django.core.files.storage import default_storage
import json # For AJAX error responses if you re-add them later

from hotel_bot_app.models import Issue, Comment, InvitedUser, IssueStatus
from hotel_bot_app.forms import IssueUpdateForm, CommentForm, IssueForm

import logging
logger = logging.getLogger(__name__)

# Define a check for staff users (Django's concept of admin users)
def is_staff_user(user):
	return user.is_authenticated and user.is_staff # or user.is_superuser
from hotel_bot_app.models import *
from django.db.models import Count, Q # Q object can be useful for complex queries if needed
from django.db import connection # Added for raw SQL
from django.db.models import Min, Max


import uuid
# Create your views here.

@login_required
@user_passes_test(is_staff_user)
def admin_issue_create(request):
	import uuid
	available_users = InvitedUser.objects.all()
	is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
	if request.method == 'POST':
		form = IssueForm(request.POST, request.FILES)
		if form.is_valid():
			issue = form.save(commit=False)
			# Always assign a valid InvitedUser to issue.created_by
			invited_user = InvitedUser.objects.filter(email__iexact=request.user.email).first()

# If not found, fallback to a default system admin invited user
			if not invited_user:
				invited_user, _ = InvitedUser.objects.get_or_create(
					email="system_admin@internal.local",
					defaults={"name": "System Admin", "password": b"", "status": "activated"}
					)
			issue.created_by = invited_user

			issue.save()
			form.save_m2m()

			# Handle observers
			observer_ids = request.POST.getlist('observers')
			if observer_ids:
				issue.observers.set(InvitedUser.objects.filter(id__in=observer_ids))

			# --- Process images and video like normal user flow ---
			media_info = []
			images = form.cleaned_data.get('images', [])
			video = form.cleaned_data.get('video')
			for img in images:
				if img.size > 4 * 1024 * 1024:
					continue
				file_name = default_storage.save(f"issues/comments/admin/{issue.id}/{uuid.uuid4()}_{img.name}", img)
				media_info.append({
					"type": "image",
					"url": default_storage.url(file_name),
					"name": img.name,
					"size": img.size
				})
			if video:
				if video.size <= 100 * 1024 * 1024:
					file_name = default_storage.save(f"issues/comments/admin/{issue.id}/{uuid.uuid4()}_{video.name}", video)
					media_info.append({
						"type": "video",
						"url": default_storage.url(file_name),
						"name": video.name,
						"size": video.size
					})

			# Always create initial comment (even if no media) so chat is not empty
			Comment.objects.create(
				issue=issue,
				commenter=request.user,
				text_content=form.cleaned_data.get('description', ''),
				media=media_info
			)

			success_message = f"Issue #{issue.id} created successfully."
			if is_ajax:
				return JsonResponse({
					'success': True,
					'message': success_message,
					'redirect_url': reverse('admin_dashboard:admin_issue_detail', args=[issue.id])
				})
			messages.success(request, success_message)
			return redirect('admin_dashboard:admin_issue_detail', issue_id=issue.id)
		else:
			error_message = "Please correct the errors below."
			if is_ajax:
				return JsonResponse({'success': False, 'errors': form.errors, 'message': error_message}, status=400)
			messages.error(request, error_message)
	else:
		form = IssueForm()
	context = {
		'form': form,
		'available_users': available_users,
		'form_action': reverse('admin_dashboard:admin_issue_create'),
	}
	return render(request, 'admin_dashboard/issues/admin_issue_create.html', context)
@login_required
def my_view(request):
	try:
		username = request.POST['username']
		password = request.POST['password']
		user = authenticate(username=username, password=password)
		print("user>>>>>>",user)
		if user is not None:
			if user.is_active:
				login(request, user)
				return redirect("admin_dashboard:dashboard")
	except Exception as e:
		print("error in my_view :::::::::::",e)
	return redirect("/admin/login")

@login_required
def change_password(request):
	try:
		if request.method == 'POST':
			form = PasswordChangeForm(request.user, request.POST)
			if form.is_valid():
				print('valid change password form')
				user = form.save()
				messages.success(request,"Password Changed Successfully")
				return HttpResponseRedirect('change-password')
			else:
				print ('in valid change password form')
		else:
			form = PasswordChangeForm(request.user)

		return render(request, 'registration/change_password.html', {
			'form': form,'page_name':'Change Password'
			})

	except Exception as e:
		print("Error in change_password :::::::::::::::::::: ",e)
		messages.error(request,"Error on server end, Please contact developer.")

def show_login(request):
	try:
		print("user id :::: ",request.user)
		if request.user.id is not None:
				return redirect('admin_dashboard:dashboard')
		else:
			return redirect('user_login')
	except Exception as e:
		print('error in  show_login',str(e))
	return redirect('admin_dashboard:dashboard')

@login_required
def logout_view(request):
	try:
		logout(request)
		return HttpResponseRedirect("/admin/login")
	except Exception as e:
		print("error in logout :::::::::::",e)

@login_required
@user_passes_test(is_staff_user)
def admin_issue_list(request):
	issues = Issue.objects.all().order_by('-created_at').select_related('created_by', 'assignee')

	# Filtering
	status = request.GET.get('status')
	issue_type = request.GET.get('type')
	created_by = request.GET.get('created_by')
	assignee = request.GET.get('assignee')
	q = request.GET.get('q')

	if q:
		issues = issues.filter(title__icontains=q)
	if status:
		issues = issues.filter(status=status)
	if issue_type:
		issues = issues.filter(type=issue_type)
	if created_by:
		issues = issues.filter(created_by__id=created_by)
	if assignee:
		issues = issues.filter(assignee__id=assignee)

	paginator = Paginator(issues, 25)
	page_number = request.GET.get('page')
	try:
		issues_page = paginator.page(page_number)
	except PageNotAnInteger:
		issues_page = paginator.page(1)
	except EmptyPage:
		issues_page = paginator.page(paginator.num_pages)

	# For filter dropdowns
	all_users = InvitedUser.objects.all()
	all_statuses = Issue._meta.get_field('status').choices
	all_types = Issue._meta.get_field('type').choices

	context = {
		'issues_page': issues_page,
		'all_users': all_users,
		'all_statuses': all_statuses,
		'all_types': all_types,
		'selected_status': status,
		'selected_type': issue_type,
		'selected_created_by': created_by,
		'selected_assignee': assignee,
		'search_query': q,
	}
	return render(request, 'admin_dashboard/issues/admin_issue_list.html', context)

@login_required
@user_passes_test(is_staff_user)
def admin_issue_edit(request, issue_id):
	issue = get_object_or_404(Issue, id=issue_id)
	print("issue ::",  issue.related_product)
	available_users = InvitedUser.objects.all()
	is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'

	if request.method == 'POST':
		print("request.POST data in admin_issue_edit:", request.POST)
		form = IssueUpdateForm(request.POST, request.FILES, instance=issue)
		if form.is_valid():
			observer_ids = request.POST.getlist('observers')
			issue_instance = form.save(commit=False)  # Save partially to handle observers before final save

			if observer_ids:
				issue_instance.observers.set(
					InvitedUser.objects.filter(id__in=observer_ids)
				)
			# If no observer_ids submitted, keep existing observers unchanged

			issue_instance.save()  # You missed this in the original snippet
			form.save_m2m()  # Save other M2M fields

			success_message = f"Issue #{issue.id} updated successfully."
			messages.success(request, success_message)
			if is_ajax:
				return JsonResponse({
					'success': True,
					'message': success_message,
					'redirect_url': reverse('admin_dashboard:admin_issue_detail', args=[issue.id])
				})
			return redirect('admin_dashboard:admin_issue_detail', issue_id=issue.id)
		else:
			error_message = "Please correct the errors below."
			messages.error(request, error_message)
			if is_ajax:
				return JsonResponse({'success': False, 'errors': form.errors, 'message': error_message}, status=400)
			# Non-AJAX will fall through to render the form with errors
	else:
		form = IssueUpdateForm(instance=issue)

	# Fetch the initial comment (first by created_at) for previewing images/videos
	initial_comment = issue.comments.order_by('created_at').first()
	initial_comment_media = initial_comment.media if initial_comment and initial_comment.media else []

	context = {
		'form': form,
		'issue': issue,
		'available_users': available_users,
		'observers': issue.observers.all(),
		'form_action': reverse('admin_dashboard:admin_issue_edit', args=[issue.id]),
		'initial_comment_media': initial_comment_media,
	}
	return render(request, 'admin_dashboard/issues/admin_issue_form.html', context)


@login_required
@user_passes_test(is_staff_user)
def admin_issue_detail(request, issue_id):
	issue = get_object_or_404(Issue, id=issue_id)
	comments = issue.comments.all().select_related('content_type')
	
	# Force evaluation of GenericForeignKey
	for comment in comments:
		_ = comment.commenter

	comment_form = CommentForm()

	# Determine both Django User and matching InvitedUser for current user
	current_user = request.user
	current_user_invited = None
	if hasattr(current_user, 'email'):
		current_user_invited = InvitedUser.objects.filter(email__iexact=current_user.email).first()

	comment_data = []
	for comment in comments:
		commenter = comment.commenter
		is_by_current_user = False
		# Robustly check if the comment is by the current user (Django User or InvitedUser)
		if commenter is not None:
			if type(commenter) == type(current_user) and hasattr(commenter, 'pk') and hasattr(current_user, 'pk'):
				if commenter.pk == current_user.pk:
					is_by_current_user = True
			# Also check for InvitedUser match if current_user_invited exists
			elif current_user_invited and type(commenter) == type(current_user_invited) and hasattr(commenter, 'pk') and hasattr(current_user_invited, 'pk'):
				if commenter.pk == current_user_invited.pk:
					is_by_current_user = True

		# Unify name for current user's comments: show email, else show name as before
		if is_by_current_user:
			# Prefer email if available
			if hasattr(current_user, 'email') and current_user.email:
				commenter_name = current_user.email
			elif current_user_invited and hasattr(current_user_invited, 'email') and current_user_invited.email:
				commenter_name = current_user_invited.email
			else:
				commenter_name = str(current_user)
		else:
			commenter_name = str(commenter)

		comment_data.append({
			"comment_id": comment.id,
			"text_content": comment.text_content,
			"media": comment.media,
			"commenter_id": getattr(commenter, "id", None),
			"commenter_name": commenter_name,
			"is_by_current_user": is_by_current_user
		})

	context = {
		'issue': issue,
		'comments': comments,
		'comment_form': comment_form,
		'user': request.user,
		'can_comment': True,
		'comment_data': comment_data,  # <-- Added this
	}
	return render(request, 'admin_dashboard/issues/admin_issue_detail.html', context)

@login_required
@user_passes_test(is_staff_user)
def admin_comment_create(request, issue_id):
	issue = get_object_or_404(Issue, id=issue_id)
	admin_user = request.user

	if request.method == 'POST':
		form = CommentForm(request.POST, request.FILES)
		if form.is_valid():
			comment = form.save(commit=False)
			comment.commenter = admin_user
			comment.issue = issue
			
			media_info = []
			images = form.cleaned_data.get('images', [])
			video = form.cleaned_data.get('video')

			for img in images:
				if img.size > 4 * 1024 * 1024: # 4MB
					messages.error(request, f"Image '{img.name}' exceeds 4MB limit.")
					continue
				file_name = default_storage.save(f"issues/comments/admin/{issue.id}/{uuid.uuid4()}_{img.name}", img)
				media_info.append({"type": "image", "url": default_storage.url(file_name), "name": img.name, "size": img.size})

			if video:
				if video.size > 100 * 1024 * 1024: # 100MB
					messages.error(request, f"Video '{video.name}' exceeds 100MB limit.")
				else:
					file_name = default_storage.save(f"issues/comments/admin/{issue.id}/{uuid.uuid4()}_{video.name}", video)
					media_info.append({"type": "video", "url": default_storage.url(file_name), "name": video.name, "size": video.size})

			comment.media = media_info
			comment.save()
			messages.success(request, "Admin comment added successfully.")
			return redirect('admin_dashboard:admin_issue_detail', issue_id=issue.id)
		else:
			messages.error(request, "Error submitting admin comment.")
			comments_qs = issue.comments.all().select_related('content_type')
			for c_item in comments_qs:
				_ = c_item.commenter
			context = {
				'issue': issue,
				'comments': comments_qs,
				'comment_form': form,
				'user': request.user
			}
			return render(request, 'admin_dashboard/issues/admin_issue_detail.html', context)
	else:
		return redirect('admin_dashboard:admin_issue_detail', issue_id=issue.id)

def _dictfetchall(cursor):
	"""Return all rows from a cursor as a list of dictionaries."""
	columns = [col[0] for col in cursor.description]
	return [
		dict(zip(columns, row))
		for row in cursor.fetchall()
	]

def _prepare_floor_progress_data():
	"""
	Prepares the data for the floor renovation progress table using raw SQL.
	Also prepares a summary of floor renovation statuses.
	Returns a tuple: (floor_progress_list, total_project_rooms, total_project_fully_completed_rooms, floor_status_summary)
	"""
	floor_progress_list = []
	total_project_rooms_accumulator = 0
	total_project_fully_completed_rooms_accumulator = 0

	renovated_floor_numbers = []
	closed_floor_numbers = [] # Floors in progress
	pending_floor_numbers = []

	sql_query = """
		WITH room_installs AS (
			-- Get all rooms that have products installed
			SELECT 
				rd.id AS room_data_id,
				rd.room AS room_number,
				rd.floor AS floor_number,
				i.id AS install_id,
				i.prework_check_on,
				i.day_install_complete,
				i.post_work_check_on,
				(SELECT COUNT(*) FROM install_detail WHERE installation_id = i.id AND status = 'YES') AS installed_products_count
			FROM
				room_data rd
			LEFT JOIN
				install i ON rd.room = i.room
			WHERE
				rd.floor IS NOT NULL
		)
		SELECT
			ri.floor_number,
			COUNT(DISTINCT ri.room_data_id) AS total_rooms_on_floor,
			COALESCE(SUM(CASE WHEN ri.prework_check_on IS NOT NULL THEN 1 ELSE 0 END), 0) AS prework_completed_count,
			-- Count a room as having installation completed if it has ANY products installed
			COALESCE(SUM(CASE WHEN ri.installed_products_count > 0 THEN 1 ELSE 0 END), 0) AS install_completed_count,
			COALESCE(SUM(CASE WHEN ri.post_work_check_on IS NOT NULL THEN 1 ELSE 0 END), 0) AS postwork_completed_count,
			COALESCE(SUM(CASE 
				WHEN ri.prework_check_on IS NOT NULL AND 
					 ri.installed_products_count > 0 AND 
					 ri.post_work_check_on IS NOT NULL 
				THEN 1 ELSE 0 
			END), 0) AS fully_completed_rooms_on_floor
		FROM
			room_installs ri
		GROUP BY
			ri.floor_number
		ORDER BY
			ri.floor_number;
	"""

	with connection.cursor() as cursor:
		cursor.execute(sql_query)
		results = _dictfetchall(cursor)

	for row in results:
		current_floor = row['floor_number']
		total_rooms_on_floor = int(row['total_rooms_on_floor'])
		prework_completed = int(row['prework_completed_count'])
		install_completed = int(row['install_completed_count'])
		postwork_completed = int(row['postwork_completed_count'])
		fully_completed_on_floor = int(row['fully_completed_rooms_on_floor'])

		total_project_rooms_accumulator += total_rooms_on_floor
		total_project_fully_completed_rooms_accumulator += fully_completed_on_floor

		percentage_completed_str = "0%"
		prework_status = "Pending"
		install_status_str = "Pending"
		postwork_status = "Pending"

		if total_rooms_on_floor > 0:
			# Calculate percentage based on total installation progress, not just fully completed rooms
			# This gives a more accurate progress percentage
			prework_percentage = (prework_completed / total_rooms_on_floor) if total_rooms_on_floor > 0 else 0
			install_percentage = (install_completed / total_rooms_on_floor) if total_rooms_on_floor > 0 else 0
			postwork_percentage = (postwork_completed / total_rooms_on_floor) if total_rooms_on_floor > 0 else 0
			
			# Overall progress is weighted average of the three phases
			# 25% prework + 50% installation + 25% postwork
			overall_percentage = (0.25 * prework_percentage + 0.5 * install_percentage + 0.25 * postwork_percentage) * 100
			percentage_completed_str = f"{overall_percentage:.0f}%"

			# Status text for each phase
			prework_status = "Completed" if prework_completed == total_rooms_on_floor else \
							 f"{(prework_percentage * 100):.0f}%" if prework_completed > 0 else "Pending"
			
			if install_completed == total_rooms_on_floor:
				install_status_str = "Completed"
			elif install_completed > 0:
				install_status_str = f"{(install_percentage * 100):.0f}%"
			# else install_status_str remains "Pending"

			postwork_status = "Completed" if postwork_completed == total_rooms_on_floor else \
							  f"{(postwork_percentage * 100):.0f}%" if postwork_completed > 0 else "Pending"
		
		floor_progress_list.append({
			'floor_number': current_floor,
			'percentage_completed': percentage_completed_str,
			'prework_status': prework_status,
			'install_status': install_status_str,
			'postwork_status': postwork_status,
		})

		# --- Categorize floors for summary --- 
		if total_rooms_on_floor > 0: # Ensure floor has rooms to be considered
			if fully_completed_on_floor == total_rooms_on_floor:
				renovated_floor_numbers.append(current_floor)
			elif install_completed > 0: # Some products installed, but not all rooms fully completed
				closed_floor_numbers.append(current_floor)
			else: # No products installed
				pending_floor_numbers.append(current_floor)
		else: # Floors with no rooms in room_data but potentially in schedule (not covered by this SQL)
			pending_floor_numbers.append(current_floor) # Or handle as per broader project definition if available
	
	floor_status_summary = {
		'renovated': {
			'count': len(renovated_floor_numbers),
			'numbers': sorted(list(set(renovated_floor_numbers))) # Ensure unique and sorted
		},
		'closed': {
			'count': len(closed_floor_numbers),
			'numbers': sorted(list(set(closed_floor_numbers)))
		},
		'pending': {
			'count': len(pending_floor_numbers),
			'numbers': sorted(list(set(pending_floor_numbers)))
		}
	}
	
	return floor_progress_list, total_project_rooms_accumulator, total_project_fully_completed_rooms_accumulator, floor_status_summary

def _prepare_pie_chart_data(total_project_rooms, total_project_fully_completed_rooms):
	"""
	Prepares the data for the overall project completion pie chart.
	"""
	# Get additional detailed information about partial completions
	with connection.cursor() as cursor:
		cursor.execute("""
			SELECT
				-- Count installations that have ANY products installed
				COUNT(DISTINCT i.id) AS installations_with_products,
				-- Count installations with completed prework
				COUNT(DISTINCT CASE WHEN i.prework = 'YES' THEN i.id END) AS prework_completed,
				-- Count installations with completed postwork
				COUNT(DISTINCT CASE WHEN i.post_work = 'YES' THEN i.id END) AS postwork_completed
			FROM
				install i
			INNER JOIN
				install_detail id ON i.id = id.installation_id
			WHERE
				id.status = 'YES'
		""")
		results = cursor.fetchone()
		
		installations_with_products = results[0] if results[0] is not None else 0
		prework_completed = results[1] if results[1] is not None else 0
		postwork_completed = results[2] if results[2] is not None else 0
	
	# Calculate weighted completion percentage
	if total_project_rooms > 0:
		# Phase weighting: 25% prework, 50% installation, 25% postwork
		prework_weight = 0.25
		install_weight = 0.50
		postwork_weight = 0.25
		
		prework_percentage = prework_completed / total_project_rooms
		install_percentage = installations_with_products / total_project_rooms
		postwork_percentage = postwork_completed / total_project_rooms
		
		overall_completion_percentage = (
			prework_weight * prework_percentage +
			install_weight * install_percentage +
			postwork_weight * postwork_percentage
		) * 100
	else:
		overall_completion_percentage = 0
	
	pending_completion_percentage = 100 - overall_completion_percentage

	return {
		'completed': round(overall_completion_percentage, 1),
		'pending': round(pending_completion_percentage, 1),
	}

EXPECTED_ROOM_TIMES = {
	'pre_work': 14,
	'install': 14,
	'post_work': 7,
	'total': 35
}

EXPECTED_FLOOR_TIMES = {
	'pre_work': 14,
	'install': 14,
	'post_work': 7,
	'total': 35
}

def _prepare_efficiency_data():
	"""
	Calculates average phase completion times for rooms and floors using raw SQL.
	Returns a dictionary with average times and expected times for both.
	"""
	# Initialize results
	avg_room_times = {'pre_work': 0, 'install': 0, 'post_work': 0, 'total': 0}
	avg_floor_times = {'pre_work': 0, 'install': 0, 'post_work': 0, 'total': 0}

	# 1. Calculate Room-Level Average Durations
	room_sql_query = """
		SELECT
			AVG(CASE
				WHEN i.prework_check_on IS NOT NULL AND s.floor_closes IS NOT NULL AND i.prework_check_on >= s.floor_closes
				THEN EXTRACT(EPOCH FROM (i.prework_check_on - s.floor_closes)) / 86400.0
				ELSE NULL
			END) AS avg_room_pre_work_duration,

			AVG(CASE
				WHEN i.day_install_complete IS NOT NULL AND i.prework_check_on IS NOT NULL AND i.day_install_complete >= i.prework_check_on
				THEN EXTRACT(EPOCH FROM (i.day_install_complete - i.prework_check_on)) / 86400.0
				ELSE NULL
			END) AS avg_room_install_duration,

			AVG(CASE
				WHEN i.post_work_check_on IS NOT NULL AND i.day_install_complete IS NOT NULL AND i.post_work_check_on >= i.day_install_complete
				THEN EXTRACT(EPOCH FROM (i.post_work_check_on - i.day_install_complete)) / 86400.0
				ELSE NULL
			END) AS avg_room_post_work_duration

		FROM install i
		JOIN room_data r ON r.room = i.room
		JOIN schedule s ON s.floor = r.floor;
	"""
	with connection.cursor() as cursor:
		cursor.execute(room_sql_query)
		result = cursor.fetchone()
		if result:
			avg_room_times['pre_work'] = round(result[0], 0) if result[0] is not None else 0
			avg_room_times['install'] = round(result[1], 0) if result[1] is not None else 0
			avg_room_times['post_work'] = round(result[2], 0) if result[2] is not None else 0
			avg_room_times['total'] = sum(filter(None, [avg_room_times['pre_work'], avg_room_times['install'], avg_room_times['post_work']]))
			avg_room_times['total'] = round(avg_room_times['total'],0)

	# 2. Calculate Floor-Level Average Durations
	floor_sql_query = """
		WITH FloorPhaseActuals AS (
			SELECT
				rd.floor,
				s.floor_closes AS actual_floor_pre_work_start,
				MAX(i.prework_check_on) AS actual_floor_pre_work_end,
				MAX(i.day_install_complete) AS actual_floor_install_end,
				MAX(i.post_work_check_on) AS actual_floor_post_work_end
			FROM
				install i
			JOIN
				room_data rd ON i.room = rd.room
			JOIN
				schedule s ON rd.floor = s.floor
			WHERE 
				rd.floor IS NOT NULL
			GROUP BY
				rd.floor, s.floor_closes
		),
		FloorPhaseDurations AS (
			SELECT
				floor,
				CASE 
					WHEN actual_floor_pre_work_end IS NOT NULL AND actual_floor_pre_work_start IS NOT NULL AND actual_floor_pre_work_end >= actual_floor_pre_work_start
					THEN EXTRACT(EPOCH FROM (actual_floor_pre_work_end - actual_floor_pre_work_start)) / 86400.0
					ELSE NULL 
				END AS floor_pre_work_duration,
				CASE
					WHEN actual_floor_install_end IS NOT NULL AND actual_floor_pre_work_end IS NOT NULL AND actual_floor_install_end >= actual_floor_pre_work_end
					THEN EXTRACT(EPOCH FROM (actual_floor_install_end - actual_floor_pre_work_end)) / 86400.0
					ELSE NULL
				END AS floor_install_duration,
				CASE
					WHEN actual_floor_post_work_end IS NOT NULL AND actual_floor_install_end IS NOT NULL AND actual_floor_post_work_end >= actual_floor_install_end
					THEN EXTRACT(EPOCH FROM (actual_floor_post_work_end - actual_floor_install_end)) / 86400.0
					ELSE NULL
				END AS floor_post_work_duration
			FROM
				FloorPhaseActuals
		)
		SELECT
			AVG(floor_pre_work_duration) AS avg_floor_pre_work_days,
			AVG(floor_install_duration) AS avg_floor_install_days,
			AVG(floor_post_work_duration) AS avg_floor_post_work_days
		FROM
			FloorPhaseDurations;
	"""
	with connection.cursor() as cursor:
		cursor.execute(floor_sql_query)
		result = cursor.fetchone()
		if result:
			avg_floor_times['pre_work'] = round(result[0], 0) if result[0] is not None else 0
			avg_floor_times['install'] = round(result[1], 0) if result[1] is not None else 0
			avg_floor_times['post_work'] = round(result[2], 0) if result[2] is not None else 0
			avg_floor_times['total'] = sum(filter(None, [avg_floor_times['pre_work'], avg_floor_times['install'], avg_floor_times['post_work']]))
			avg_floor_times['total'] = round(avg_floor_times['total'],0)

	return {
		'room_efficiency': {
			'average_time': avg_room_times,
			'expected_time': EXPECTED_ROOM_TIMES
		},
		'floor_efficiency': {
			'average_time': avg_floor_times,
			'expected_time': EXPECTED_FLOOR_TIMES
		}
	}

def _prepare_overall_project_time_data():
	"""
	Calculates overall average room completion times for the project.
	Returns a dictionary formatted for the 'Overall Project Time' display.
	"""
	data = {}
	avg_times = {'pre_work': 0, 'install': 0, 'post_work': 0, 'total': 0}

	# Reusing the same SQL logic for average room times
	room_sql_query = """
		SELECT
			AVG(CASE
				WHEN i.prework_check_on IS NOT NULL AND s.floor_closes IS NOT NULL AND i.prework_check_on >= s.floor_closes
				THEN EXTRACT(EPOCH FROM (i.prework_check_on - s.floor_closes)) / 86400.0
				ELSE NULL
			END) AS avg_room_pre_work_duration,

			AVG(CASE
				WHEN i.day_install_complete IS NOT NULL AND i.prework_check_on IS NOT NULL AND i.day_install_complete >= i.prework_check_on
				THEN EXTRACT(EPOCH FROM (i.day_install_complete - i.prework_check_on)) / 86400.0
				ELSE NULL
			END) AS avg_room_install_duration,

			AVG(CASE
				WHEN i.post_work_check_on IS NOT NULL AND i.day_install_complete IS NOT NULL AND i.post_work_check_on >= i.day_install_complete
				THEN EXTRACT(EPOCH FROM (i.post_work_check_on - i.day_install_complete)) / 86400.0
				ELSE NULL
			END) AS avg_room_post_work_duration

		FROM install i
		JOIN room_data r ON r.room = i.room
		JOIN schedule s ON s.floor = r.floor;

	"""
	with connection.cursor() as cursor:
		cursor.execute(room_sql_query)
		result = cursor.fetchone()
		if result:
			avg_times['pre_work'] = round(result[0], 0) if result[0] is not None else 0
			avg_times['install'] = round(result[1], 0) if result[1] is not None else 0
			avg_times['post_work'] = round(result[2], 0) if result[2] is not None else 0
			avg_times['total'] = sum(filter(None, [avg_times['pre_work'], avg_times['install'], avg_times['post_work']]))
			avg_times['total'] = round(avg_times['total'],0)

	phases = ['pre_work', 'install', 'post_work', 'total']
	for phase in phases:
		avg = avg_times[phase]
		expected = EXPECTED_ROOM_TIMES[phase]
		data[phase] = {
			'avg': avg,
			'expected': expected,
			'is_delayed': avg > expected
		}
	return data

def _calculate_date_details(schedule_date, actual_date, is_start_date_field=True):
	"""
	Calculates difference, status, and CSS class for a pair of schedule/actual dates.
	Difference is schedule_date - actual_date.
	"""
	details = {
		'schedule': schedule_date,
		'actual': actual_date,
		'difference': None,
		'status': '',
		'css_class': 'status-neutral'  # Default, e.g., for N/A values
	}

	if actual_date and schedule_date:
		try:
			# Ensure both are datetime.date objects if they are datetime.datetime
			if hasattr(schedule_date, 'date'):
				schedule_date = schedule_date.date()
			if hasattr(actual_date, 'date'):
				actual_date = actual_date.date()
			difference_days = (schedule_date - actual_date).days
		except TypeError: # Handle cases where one is date and other is datetime or other type issues
			difference_days = (schedule_date - actual_date).days if hasattr(schedule_date, 'date') and hasattr(actual_date, 'date') else None

		details['difference'] = difference_days
		if difference_days is not None:
			if is_start_date_field:
				if difference_days >= 0: # actual_date <= schedule_start_date (on time or early)
					details['status'] = 'STARTED'
					details['css_class'] = 'status-ok'
				else: # actual_date > schedule_start_date (delay)
					details['status'] = 'DELAY'
					details['css_class'] = 'status-delay'
			else: # End date field
				if difference_days >= 0: # actual_end_date <= schedule_end_date (on time or early)
					details['status'] = 'OK'
					details['css_class'] = 'status-ok'
				else: # actual_end_date > schedule_end_date (delay)
					details['status'] = 'DELAY'
					details['css_class'] = 'status-delay'
		else: # difference_days is None, implies a type issue before
			details['status'] = 'DATE ERROR'

	elif schedule_date: # Actual date is None, but schedule exists
		if is_start_date_field:
			details['status'] = 'NOT STARTED'
		else:
			details['status'] = 'NOT ENDED'
	else: # Schedule date is None (and actual might be None or present)
		details['status'] = 'N/A'
		if actual_date and not is_start_date_field : # If actual end date exists but no schedule end
			details['status'] = 'ENDED (NO SCHEDULE)'
		elif actual_date and is_start_date_field : # If actual start date exists but no schedule start
			details['status'] = 'STARTED (NO SCHEDULE)'

	return details

def _prepare_room_detail_report_context(request, room_number_query):
	"""
	Prepares the context data for the Detail Report by Room section using direct SQL.
	Fetches room, schedule, and install data, calculates phase details,
	and handles messages for errors or warnings.
	"""
	report_context = {
		'room_report_data': None,
		'queried_room_data': None, # For {{ queried_room_data.floor.floor_number }}
	}

	if not room_number_query:
		return report_context

	sql = """
		SELECT
			rd.room AS queried_room_value,
			rd.floor AS queried_floor_number,

			-- Schedule Dates from 'schedule' table
			s.pre_work_starts AS s_prework_start_date,
			s.pre_work_ends AS s_prework_end_date,
			s.install_starts AS s_install_start_date,
			s.install_ends AS s_install_end_date,
			s.post_work_starts AS s_postwork_start_date,
			s.post_work_ends AS s_postwork_end_date,
			s.floor_closes AS s_overall_start_date, -- Schedule overall start
			s.floor_opens AS s_overall_end_date,   -- Schedule overall end

			-- Actual Dates from 'install' table
			s.pre_work_starts AS a_prework_start_date, -- Assuming 'day_prework_began' is not in 'install' or not used for actual prework start
			i.prework_check_on AS a_prework_end_date,
			i.day_install_began AS a_install_start_date,
			i.day_install_complete AS a_install_end_or_postwork_start_date, -- Actual install end is also actual post-work start
			i.post_work_check_on AS a_postwork_end_date
		FROM
			room_data rd
		LEFT JOIN
			schedule s ON s.floor = rd.floor
		LEFT JOIN
			install i ON i.room = rd.room
		WHERE
			rd.room = %s;
	"""

	try:
		# Initialize all date variables to None to prevent NameError if data fetching fails partially
		s_prework_start, s_prework_end, a_prework_start, a_prework_end = None, None, None, None
		s_install_start, s_install_end, a_install_start, a_install_end = None, None, None, None
		s_postwork_start, s_postwork_end, a_postwork_start, a_postwork_end = None, None, None, None
		s_overall_start, s_overall_end, a_overall_start, a_overall_end = None, None, None, None

		with connection.cursor() as cursor:
			cursor.execute(sql, [room_number_query])
			result_row = _dictfetchall(cursor) # _dictfetchall returns a list

		if not result_row:
			messages.error(request, f"Room number {room_number_query} not found or no associated data.")
			return report_context
		
		data = result_row[0] # We expect only one row for a unique room number

		# Populate queried_room_data for template {{ queried_room_data.floor.floor_number }}
		report_context['queried_room_data'] = {
			'floor': {'floor_number': data.get('queried_floor_number')}
		}

		room_report_data = {}

		# Extract schedule dates directly from the new SQL aliases
		s_prework_start = data.get('s_prework_start_date')
		s_prework_end = data.get('s_prework_end_date')
		# Actual prework start is still assumed NULL or sourced differently if available
		a_prework_start = data.get('a_prework_start_date') # Will be NULL from SQL
		a_prework_end = data.get('a_prework_end_date')
		room_report_data['prework'] = {
			'start': _calculate_date_details(s_prework_start, a_prework_start, is_start_date_field=True),
			'end': _calculate_date_details(s_prework_end, a_prework_end, is_start_date_field=False),
		}

		s_install_start = data.get('s_install_start_date')
		s_install_end = data.get('s_install_end_date')
		a_install_start = data.get('a_install_start_date')
		a_install_end = data.get('a_install_end_or_postwork_start_date') # Actual install ends
		room_report_data['install'] = {
			'start': _calculate_date_details(s_install_start, a_install_start, is_start_date_field=True),
			'end': _calculate_date_details(s_install_end, a_install_end, is_start_date_field=False),
		}

		s_postwork_start = data.get('s_postwork_start_date')
		s_postwork_end = data.get('s_postwork_end_date')
		a_postwork_start = data.get('a_install_end_or_postwork_start_date') # Actual post-work starts when install is completed
		a_postwork_end = data.get('a_postwork_end_date')
		room_report_data['postwork'] = {
			'start': _calculate_date_details(s_postwork_start, a_postwork_start, is_start_date_field=True),
			'end': _calculate_date_details(s_postwork_end, a_postwork_end, is_start_date_field=False),
		}
		
		# OVERALL Calculation
		# Scheduled overall dates from new SQL aliases
		s_overall_start = data.get('s_overall_start_date')
		s_overall_end = data.get('s_overall_end_date')
		
		# Actual overall dates are derived from min/max of actual phase dates
		all_actual_starts = [d for d in [a_prework_start, a_install_start, data.get('a_install_end_or_postwork_start_date') # using data.get for a_postwork_start
										] if d] #
		all_actual_ends = [d for d in [a_prework_end, a_install_end, a_postwork_end] if d]

		a_overall_start = min(all_actual_starts) if all_actual_starts else None
		a_overall_end = max(all_actual_ends) if all_actual_ends else None
		
		room_report_data['overall'] = {
			'start': _calculate_date_details(s_overall_start, a_overall_start, is_start_date_field=True),
			'end': _calculate_date_details(s_overall_end, a_overall_end, is_start_date_field=False),
		}
		report_context['room_report_data'] = room_report_data

	except Exception as e_sql_report:
		logger.error(f"SQL Error or data processing error in room detail report for {room_number_query}: {e_sql_report}")
		messages.error(request, f"An error occurred while generating the detailed report for room {room_number_query}.")
		# report_context already has None for data fields

	return report_context

@login_required
def dashboard(request):
	try:
		floor_progress_list, total_rooms, completed_rooms, floor_summary = _prepare_floor_progress_data()
		pie_chart_data = _prepare_pie_chart_data(total_rooms, completed_rooms)
		efficiency_data = _prepare_efficiency_data()
		overall_project_time_data = _prepare_overall_project_time_data()

		# Fetch issues for Hotel Admin
		hotel_admin_issues_qs = Issue.objects.filter(is_for_hotel_owner=True)\
									  .select_related('created_by', 'assignee')\
									  .order_by('-created_at')
		
		open_statuses = [IssueStatus.OPEN, IssueStatus.PENDING, IssueStatus.WORKING]
		hotel_admin_open_issues = hotel_admin_issues_qs.filter(status__in=open_statuses)
		hotel_admin_closed_issues = hotel_admin_issues_qs.filter(status=IssueStatus.CLOSE)
		
		context = {
			'floor_progress_data': floor_progress_list,
			'pie_chart_data': pie_chart_data,
			'floor_status_summary': floor_summary,
			'efficiency_data': efficiency_data,
			'overall_project_time': overall_project_time_data,
			'page_name': 'Dashboard',
			'room_report_data': None, 
			'queried_room_data': None,
			'room_number_query': '',
			'hotel_admin_open_issues': hotel_admin_open_issues,
			'hotel_admin_closed_issues': hotel_admin_closed_issues,
		}

		room_number_query = request.GET.get('room_number', '').strip()
		context['room_number_query'] = room_number_query

		if room_number_query:
			room_report_specific_context = _prepare_room_detail_report_context(request, room_number_query)
			print(f"Room report specific context: {room_report_specific_context}")
			context.update(room_report_specific_context)

		return render(
			request, "dashboard.html", context
		)
	except Exception as e:
		logger.error(f"Generic error in dashboard view: {e}")
		messages.error(request, "An error occurred while loading the dashboard.")
		return redirect("admin_dashboard:login") 

@login_required
@user_passes_test(is_staff_user)
def hotel_admin_issue_dashboard(request):
	# Common queryset for issues intended for hotel owner/admin
	base_issues_qs = Issue.objects.filter(is_for_hotel_owner=True)\
								  .select_related('created_by', 'assignee')\
								  .order_by('-created_at')

	search_query = request.GET.get('q', '')
	if search_query:
		base_issues_qs = base_issues_qs.filter(
			Q(title__icontains=search_query) |
			Q(description__icontains=search_query) |
			Q(id__icontains=search_query) # Assuming ID can be searched
		)

	open_statuses = [IssueStatus.OPEN, IssueStatus.PENDING, IssueStatus.WORKING]
	
	open_issues = base_issues_qs.filter(status__in=open_statuses)
	closed_issues = base_issues_qs.filter(status=IssueStatus.CLOSE)

	# Note: Datatables handles its own pagination, so Django's Paginator is not used here directly
	# for the table rendering, but you might need it if you were to implement server-side processing
	# for datatables with very large datasets.

	context = {
		'open_issues': open_issues,
		'closed_issues': closed_issues,
		'search_query': search_query,
		'page_name': 'Hotel Admin Issues'
	}
	return render(request, 'admin_dashboard/hotel_admin_issue_dashboard.html', context)

@login_required
@user_passes_test(is_staff_user)
def admin_issue_create(request):
	is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
	available_users = InvitedUser.objects.all()

	if request.method == 'POST':
		form = IssueUpdateForm(request.POST, request.FILES)
		try:
			if form.is_valid():
				issue = form.save(commit=False)

				# Determine creator (InvitedUser)
				invited_user_instance = None
				try:
					if isinstance(request.user, InvitedUser):
						invited_user_instance = request.user
					else:
						invited_user_instance = InvitedUser.objects.filter(id=request.user.id).first()
						if not invited_user_instance:
							invited_user_instance = InvitedUser.objects.filter(email__iexact=request.user.email).first()

					if not invited_user_instance:
						error_msg = "Associated invited user account not found."
						if is_ajax:
							return JsonResponse({'success': False, 'message': error_msg}, status=400)
						messages.error(request, error_msg)
						return render(request, 'admin_dashboard/issues/admin_issue_form.html', {
							'form': form,
							'is_admin': True,
							'form_action': reverse('admin_dashboard:admin_issue_create'),
							'available_users': available_users,
							'observers': [],
						})

					issue.created_by = invited_user_instance
				except Exception as e:
					error_msg = f"Error finding associated invited user: {str(e)}"
					if is_ajax:
						return JsonResponse({'success': False, 'message': error_msg}, status=400)
					messages.error(request, error_msg)
					return render(request, 'admin_dashboard/issues/admin_issue_form.html', {
						'form': form,
						'is_admin': True,
						'form_action': reverse('admin_dashboard:admin_issue_create'),
						'available_users': available_users,
						'observers': [],
					})

				issue.save()
				form.save_m2m()

				# Set observers
				if 'observers' in form.cleaned_data:
					selected_observers = form.cleaned_data['observers']
					if selected_observers:
						issue.observers.set(selected_observers)

				# Ensure creator is observer
				issue.observers.add(invited_user_instance)

				# Process media files
				media_urls = []
				images = form.cleaned_data.get('images', [])
				video = form.cleaned_data.get('video')

				for image in images:
					file_name = default_storage.save(f"issues/images/{uuid.uuid4()}_{image.name}", image)
					file_url = default_storage.url(file_name)
					media_urls.append({
						'type': 'image',
						'url': file_url,
						'size': image.size,
						'name': image.name
					})

				if video:
					file_name = default_storage.save(f"issues/videos/{uuid.uuid4()}_{video.name}", video)
					file_url = default_storage.url(file_name)
					media_urls.append({
						'type': 'video',
						'url': file_url,
						'size': video.size,
						'name': video.name
					})

				# Create initial comment if media exists
				if media_urls:
					Comment.objects.create(
						issue=issue,
						commenter=invited_user_instance,
						text_content=form.cleaned_data.get('description', ''),
						media=media_urls
					)

				success_message = f"Issue #{issue.id} created successfully."
				if is_ajax:
					return JsonResponse({
						'success': True,
						'message': success_message,
						'redirect_url': reverse('admin_dashboard:admin_issue_detail', args=[issue.id])
					})
				messages.success(request, success_message)
				return redirect('admin_dashboard:admin_issue_detail', issue_id=issue.id)

			# Form is invalid
			error_message = "Please correct the errors below."
			if is_ajax:
				return JsonResponse({'success': False, 'errors': form.errors, 'message': error_message}, status=400)
			messages.error(request, error_message)

		except Exception as e:
			if is_ajax:
				return JsonResponse({'success': False, 'message': f"An unexpected error occurred. ({str(e)})"}, status=500)
			messages.error(request, f"An unexpected error occurred. ({str(e)})")
			form = IssueUpdateForm(request.POST, request.FILES)  # Re-init for rendering
	else:
		form = IssueUpdateForm()

	# Populate observer users on error or GET
	context_data_observers = []
	if request.method != 'GET':
		observer_pks_from_post = request.POST.getlist('observers')
		if observer_pks_from_post:
			try:
				valid_pks = [pk for pk in observer_pks_from_post if pk.isdigit()]
				if valid_pks:
					context_data_observers = list(InvitedUser.objects.filter(pk__in=valid_pks))
			except ValueError:
				context_data_observers = []

	context = {
		'form': form,
		'is_admin': True,
		'form_action': reverse('admin_dashboard:admin_issue_create'),
		'available_users': available_users,
		'observers': context_data_observers,
	}
	return render(request, 'admin_dashboard/issues/admin_issue_form.html', context)
