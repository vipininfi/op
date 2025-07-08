
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .core.chatbot_core import InventoryChatbot
from django.http import StreamingHttpResponse
import json

@csrf_exempt
def create_session_api(request):
    """
    API endpoint to create a new chat session. Returns session id as JSON.
    Uses the Django auth user to find the InvitedUser.
    """
    if request.method == "POST":
        if not request.user.is_authenticated:
            return JsonResponse({"error": "User not logged in."}, status=401)
        # Try to find InvitedUser by email
        try:
            user = InvitedUser.objects.get(email=request.user.email)
        except InvitedUser.DoesNotExist:
            return JsonResponse({"error": "No InvitedUser found for this account."}, status=404)
        session = ChatSession.objects.create(user=user)
        return JsonResponse({"session_id": session.id})
    return JsonResponse({"error": "Invalid request method."}, status=405)
import ast
import json
import random
import string
from datetime import date, datetime
from functools import wraps
from .forms import CommentForm
import uuid
from django.db.models.functions import Lower
from django.urls import reverse
import bcrypt
import environ
import requests
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import send_mail
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils.timezone import now
from django.core.files.storage import default_storage
from django.views.decorators.csrf import csrf_exempt
from hotel_bot_app.utils.helper import (fetch_data_from_sql,
                                         format_gpt_prompt,
                                         generate_final_response,
                                         gpt_call_json_func,gpt_call_json_func_two,
                                         load_database_schema,
                                         verify_sql_query,
                                         generate_sql_prompt,
                                         generate_natural_response_prompt,
                                         output_praser_gpt,
                                         intent_detection_prompt)
from openai import OpenAI
from html import escape
import xlwt
import pytz

from django.db.models import Q
from django.contrib.auth import get_user_model # Use this if settings.AUTH_USER_MODEL is Django's default
from django.contrib.auth.decorators import login_required, user_passes_test # For permission checking
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger # Import Paginator
from django.db.models import Count, Q, F, ExpressionWrapper, DecimalField, Sum, Max, Min

from .models import *
from .models import ChatSession
from django.utils.timezone import localtime
from django.db import connection
from .forms import IssueForm, CommentForm, IssueUpdateForm # Import the forms
import logging
from django.contrib.contenttypes.models import ContentType # Added for GFK
from django.utils.timezone import make_aware
from django.http import HttpResponseForbidden

logger = logging.getLogger(__name__)

env = environ.Env()
environ.Env.read_env()

password_generated = "".join(random.choices(string.ascii_letters + string.digits, k=6))
open_ai_key = env("open_ai_api_key")

client = OpenAI(api_key=open_ai_key)

User = InvitedUser # Or User = get_user_model()

def session_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("user_id"):
            return redirect("user_login")
        return view_func(request, *args, **kwargs)
    return wrapper

def admin_required(view_func):
    """
    Decorator to check if the user is a superuser or has is_administrator=True
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # Check if user is authenticated
        if not request.user.is_authenticated:
            return redirect('login')
            
        # Check if user is superuser or administrator
        if request.user.is_superuser or hasattr(request.user, 'profile') and request.user.profile.is_administrator:
            return view_func(request, *args, **kwargs)
            
        # If not, return forbidden
        return HttpResponseForbidden("You don't have permission to access this page")
    return wrapper

def extract_values(json_obj, keys):
    """
    Extract values from a JSON object based on the provided list of keys.
    :param json_obj: Dictionary (parsed JSON object)
    :param keys: List of keys to extract values for
    :return: None (prints formatted output)
    """
    table_selected = ""
    for key in keys:
        value = json_obj.get(key, None)
        table_selected += f"Table name is '{key}' and column name is {value}\n\n"
    print("table selected ,,,,,,", table_selected)
    return table_selected


def get_chat_history_from_db(session_id):

    if not session_id:
        return JsonResponse({"chat_history": []})  # No session, return empty history

    session = get_object_or_404(ChatSession, id=session_id)
    history_messages = list(
        ChatHistory.objects.filter(session=session).values("role", "message")
    )
    converted_messages = [
    {'role': msg['role'], 'content': msg['message']}
    for msg in history_messages
    ]
    return converted_messages


@csrf_exempt
def chatbot_api(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            user_message = data.get("message", "").strip()
            session_id = data.get("session_id")
        except json.JSONDecodeError:
            return JsonResponse({"response": "⚠️ Invalid request format."}, status=400)

        if not user_message:
            return JsonResponse({"response": "⚠️ Please enter a message."}, status=400)

        # --- Session Management (stateless, uses session_id from frontend) ---
        session = None
        if session_id:
            try:
                session = ChatSession.objects.get(id=session_id)
            except ChatSession.DoesNotExist:
                session = ChatSession.objects.create()
                session_id = session.id
        else:
            session = ChatSession.objects.create()
            session_id = session.id
        print(f"Using chat session: {session_id}")


        # --- Log User Message ---
        try:
            ChatHistory.objects.create(session=session, message=user_message, role="user")
        except Exception as e:
            print(f"Error saving user message to chat history: {e}")
            # Non-critical, proceed with generation but log the issue


        # --- New Core Chatbot Logic (Two-Stage LLM) ---
        bot_message = "Sorry, I encountered an unexpected issue and couldn't process your request. Please try again later." # Default error
        final_sql_query = None # Store the query that was actually executed
        rows = None # Store the results

        try:
            # 1. Load Schema
            try:
                DB_SCHEMA = load_database_schema()
            except Exception as e:
                print(f"Critical Error loading database schema: {e}")
                raise Exception("Failed to load schema information.")

            # 2. First LLM Call: Intent Recognition & Conditional SQL Generation
            intent_response = None
            try:
                intent_prompt = intent_detection_prompt(user_message)

                intent_prompt_first_output = json.loads(gpt_call_json_func_two(
                    intent_prompt,
                    gpt_model="gpt-4o",
                    openai_key=open_ai_key,
                    json_required=True
                ))
                print('intent_prompt_first_output',intent_prompt_first_output)
                

                intent_prompt_system_prompt,intent_prompt_user_prompt = generate_sql_prompt(user_message, DB_SCHEMA)
                if 'response' not in intent_prompt_first_output:
                    print("Its a db query, as identify by intent_detection_prompt.")
                    intent_prompt_system_prompt={"role":"system","content":intent_prompt_system_prompt + f'## Relevant Context : ##{intent_prompt_first_output}'}
                else:
                    print("we got response")
                    intent_prompt_system_prompt={"role":"system","content":intent_prompt_system_prompt}

                intent_prompt_user_prompt={"role":"user","content":intent_prompt_user_prompt}
                print(1)
                if session_id!=None:
                    chat_history_memory=get_chat_history_from_db(session_id)
                    print(2)
                    
                    if len(chat_history_memory) > 5:
                        chat_history_memory = chat_history_memory[-5:]
                    
                    chat_history_memory=[intent_prompt_system_prompt]+chat_history_memory
                    
                    # print('chat_history_memory ...........',chat_history_memory)
                    sql_response = json.loads(gpt_call_json_func_two(
                        chat_history_memory,
                        gpt_model="gpt-4o",
                        openai_key=open_ai_key,
                        json_required=True
                    ))
                    print('sql_response ',sql_response)
                if session_id==None:
                    # print("session id is none",[{"role":"system","content":intent_prompt_system_prompt},{"role":"user","content":user_message}])
                    sql_response = json.loads(gpt_call_json_func_two(
                        [intent_prompt_system_prompt,{"role":"user","content":user_message}],
                        gpt_model="gpt-4o",
                        openai_key=open_ai_key,
                        json_required=True
                    ))
            except Exception as e:
                print(f"Error during Intent/SQL Generation LLM call: {e}")
                # Fall through, sql_response remains None

            if not sql_response or not isinstance(sql_response, dict):
                print("Error: Failed to get valid JSON response from Intent/SQL LLM.")
                raise Exception("Failed to understand request intent.")

            needs_sql = sql_response.get("needs_sql")
            initial_sql_query = sql_response.get("query")
            direct_answer = sql_response.get("direct_answer")

            # 3. Handle based on Intent
            if needs_sql is False and direct_answer:
                print("Intent LLM provided a direct answer.")
                bot_message = direct_answer
                # Skip SQL execution and proceed directly to logging/returning the direct answer

            elif needs_sql is True and initial_sql_query:
                print(f"\n\nGenerated query: \n\n{initial_sql_query}\n\n\n")
                final_sql_query = initial_sql_query # Tentatively set the final query

                # 4. Execute SQL (and verify/retry if needed)
                try:
                    # Initial attempt
                    rows = fetch_data_from_sql(initial_sql_query)
                    print(f"Initial query execution successful.",rows)

                except Exception as db_error:
                    print(f"Initial DB execution error: {db_error}. Attempting verification.")

                    verification = None
                    try:
                        verification = verify_sql_query(
                            user_message=user_message,
                            sql_query=initial_sql_query, # Verify the original query
                            prompt_data=DB_SCHEMA,
                            error_message=str(db_error),
                            gpt_model="gpt-4o"
                        )
                    except Exception as verify_e:
                        print(f"Error during SQL verification call: {verify_e}")

                    print(f"Verification result: {verification}")

                    if verification and isinstance(verification, dict) and not verification.get("is_valid") and verification.get("recommendation"):
                        recommended_query = verification["recommendation"]
                        print(f"Verification recommended new query: {recommended_query}")
                        try:
                            rows = fetch_data_from_sql(recommended_query)
                            final_sql_query = recommended_query # Update the final query executed
                            print("Retry with recommended query successful.",rows)
                        except Exception as second_error:
                            print(f"Retry with recommended query failed: {second_error}")
                            # Clear rows and final_sql_query as the attempt failed
                            rows = None
                            final_sql_query = initial_sql_query # Revert to indicate initial attempt failed
                            # Don't raise here, let the final response generation handle the failure state
                    else:
                        print("SQL Verification did not provide a usable correction or retry failed.")
                        # Clear rows as the query failed
                        rows = None
                        # Keep final_sql_query as the one that failed for context

                # 5. Second LLM Call: Generate Natural Language Response (if SQL was attempted)
                # This block runs whether SQL succeeded, failed, or returned no rows
                try:
                    response_prompt = generate_natural_response_prompt(user_message, final_sql_query, rows)
                    # print('response prompt is :::::::',response_prompt)
                    bot_message = output_praser_gpt( # Use output_praser_gpt as we expect text
                        response_prompt,
                        gpt_model="gpt-4o",
                        json_required=False,
                        temperature=0.7 # Allow slightly more creativity for natural language
                    )
                    if not bot_message or not isinstance(bot_message, str):
                         print(f"Error: Natural response generation returned invalid data: {bot_message}")
                         raise Exception("Failed to format the final natural response.")
                except Exception as e:
                    print(f"Error during Natural Response Generation LLM call: {e}")
                    raise Exception("Failed to generate the final natural response.")

            else:
                # Invalid state from first LLM call (e.g., needs_sql=true but no query)
                print(f"Error: Invalid state from Intent LLM. Response: {intent_response}")
                raise Exception("Received an inconsistent response from the intent analysis.")

        except Exception as e:
            print(f"Error in chatbot_api main logic: {e}")
            # Use the default error message
            # bot_message = "Sorry, I encountered an unexpected issue..."
            internal_error_message = f"Failed processing user message '{user_message}'. Error: {str(e)}"
            print(internal_error_message)
            # Ensure the default message is used if an exception occurred before natural response generation
            if bot_message == "Sorry, I encountered an unexpected issue and couldn't process your request. Please try again later.":
                 # If the default message is still set, it means we didn't reach the natural response stage or it failed.
                 pass # Keep the default message
            elif not bot_message or not isinstance(bot_message, str):
                 # If bot_message got corrupted somehow during error handling
                 bot_message = "Sorry, I encountered an unexpected issue and couldn't process your request. Please try again later."

        
        messages=bot_message
        try:
            ChatHistory.objects.create(session=session, message=messages, role="assistant")
        except Exception as e:
             print(f"Error saving assistant message to chat history: {e}")
             # Non-critical


        # --- Return Response ---
        return JsonResponse({"response": bot_message,"table_info":rows})

    # --- Handle Non-POST Requests ---
    return JsonResponse({"error": "Invalid request method. Only POST is allowed."}, status=405)

def convert_to_html_table(data):
    html = ['<table>']

    # Header
    html.append('<tr>')
    for col in data['columns']:
        html.append(f'<th>{escape(col)}</th>')
    html.append('</tr>')

    # Rows
    for row in data['rows']:
        html.append('<tr>')
        for cell in row:
            if isinstance(cell, datetime):
                cell = cell.isoformat()
            html.append(f'<td>{escape(str(cell))}</td>')
        html.append('</tr>')

    html.append('</table>')
    return '\n'.join(html)

@csrf_exempt
def get_chat_history(request):
    session_id = request.session.get("chat_session_id")

    if not session_id:
        return JsonResponse({"chat_history": []})  # No session, return empty history

    session = get_object_or_404(ChatSession, id=session_id)
    history_messages = list(
        ChatHistory.objects.filter(session=session).values("role", "message")
    )
    return JsonResponse({"chat_history": history_messages})


def chatbot(request):
    return render(request, "chatbot.html")

@login_required
@admin_required
def display_prompts(request):
    print(request)
    prompts = Prompt.objects.all()  # Fetch all records
    for prompt in prompts:
        print(prompt.id, prompt.prompt_number, prompt.description)
    return render(request, "edit_prompt.html", {"prompts": prompts})

@login_required
def update_prompt(request):
    if request.method == "POST":
        prompt_id = request.POST.get("prompt_id")  # Get the ID
        prompt_description = request.POST.get(
            "prompt_description"
        )  # Get the new description

        try:
            prompt = Prompt.objects.get(id=prompt_id)  # Fetch the object
            prompt.description = prompt_description  # Update the description
            prompt.save()  # Save changes
            print("Updated successfully")
        except Prompt.DoesNotExist:
            print("Prompt not found")

    return render(request, "edit_prompt.html")

@login_required
def user_management(request):
    # Additional check to ensure only superusers can access this view
    if not request.user.is_superuser:
        return HttpResponseForbidden("Only superusers can access this page. Administrator users cannot access user management.")
    
    # Removed the info message about the Administrator role
    prompts = InvitedUser.objects.all()
    print("users", prompts)

    return render(request, "user_management.html", {"prompts": prompts})

@login_required
def add_users_roles(request):
    print(request.POST)
    # Check if this is an AJAX request
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    try:
        if request.method == "POST":
            name = request.POST.get("name")  # Get the ID
            email = request.POST.get("email")  # Get the new description
            roles = request.POST.get("role")  # Get the new description
            status = request.POST.get("status")  # Get the new description
            password = request.POST.get("password")  # Get the password
            roles_list = roles.split(", ") if roles else []
            print(name, email, type(roles_list), roles_list, status, password)

            # Check if email already exists
            if InvitedUser.objects.filter(email=email).exists():
                return JsonResponse({"error": "User with this email already exists."}, status=400)


            user = InvitedUser.objects.create(
                name=name,
                role=roles_list,
                last_login=now(),
                email=email,
                status=status if status else 'activated', # Default to activated if not provided
                password=bcrypt.hashpw(password.encode(), bcrypt.gensalt()),
            )

            # Set is_administrator flag in InvitedUser for anyone with administrator role
            if 'administrator' in roles_list:
                user.is_administrator = True
                user.save()

                # Create Django User with administrator privileges (but not superuser)
                auth_user = User.objects.create_user(
                    username=email,
                    password=password,
                    email=email,
                )
                auth_user.is_staff = True  # Set is_staff to True for admin dashboard access
                auth_user.is_superuser = False
                auth_user.save()

                # Set is_administrator in UserProfile
                auth_user.profile.is_administrator = True
                auth_user.profile.save()

                # For AJAX requests, don't use messages framework
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        "success": True,
                        "message": f"Administrator user '{name}' created successfully."
                    })

                messages.success(request, f"Administrator user '{name}' created successfully. They can access the Django admin interface.")
            elif 'admin' in roles_list:
                # Regular admin/hotel owner role
                auth_user = User.objects.create_user(
                    username=email,
                    password=password,
                    email=email,
                )
                auth_user.is_staff = True  # Still give admin dashboard access
                auth_user.is_superuser = False
                auth_user.save()

            if User.role == 'administrator':
                request.session['is_soft_admin'] = True
            else:
                request.session['is_soft_admin'] = False

            # Return JSON response for AJAX requests
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        "success": True,
                        "message": f" User '{name}' created successfully."
                    })

        # For non-AJAX requests
        return render(request, "add_users_roles.html")
    except Exception as e:
        import traceback
        if is_ajax:
            # For AJAX requests, return JSON error response
            return JsonResponse({
                        "success": True,
                        "message": f" User '{name}' created successfully."
                    })  


@login_required
def edit_users_roles(request, user_id):
    if request.method == "POST":
        try:
            print("edit_users_roles user_id::", user_id)
            user = get_object_or_404(InvitedUser, id=user_id)
            
            name = request.POST.get("name")
            email = request.POST.get("email")
            roles = request.POST.get("role")
            status = request.POST.get("status")
            password = request.POST.get("password")

            roles_list = roles.split(", ") if roles else []

            # Check if email is being changed and if the new email already exists for another user
            if email != user.email and InvitedUser.objects.filter(email=email).exclude(id=user_id).exists():
                return JsonResponse({"error": "Another user with this email already exists."}, status=400)

            user.name = name
            user.email = email
            user.role = roles_list
            user.status = status if status else 'activated' # Default to activated

            if password: # Only update password if a new one is provided
                user.password = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
            
            user.save()

            # Check if user has administrator role
            is_administrator_role = 'administrator' in roles_list
            
            # Update is_administrator flag on InvitedUser
            user.is_administrator = is_administrator_role
            
            # Handle Django User creation/update
            if 'administrator' in roles_list or 'admin' in roles_list:
                # Check if Django auth user already exists
                try:
                    auth_user = User.objects.get(username=email)
                    auth_user.is_staff = True  # Ensure is_staff is set
                    
                    # If password was provided, update it
                    if password:
                        auth_user.set_password(password)
                    
                    # Update the is_administrator flag in the profile
                    if hasattr(auth_user, 'profile'):
                        auth_user.profile.is_administrator = is_administrator_role
                        auth_user.profile.save()
                    
                    auth_user.save()
                except User.DoesNotExist:
                    # Only create if it doesn't exist
                    if password:  # Only create if we have a password
                        auth_user = User.objects.create_user(
                            username=email,
                            password=password,
                            email=email,
                        )
                        auth_user.is_staff = True  # Set is_staff to True
                        auth_user.is_superuser = False
                        auth_user.save()
                        
                        # Set is_administrator in profile
                        auth_user.profile.is_administrator = is_administrator_role
                        auth_user.profile.save()
            else:
                try:
                    auth_user = User.objects.get(username=email)
                    auth_user.delete()
                except User.DoesNotExist:
                    pass
            return JsonResponse({"message": "User updated successfully!"})

        except InvitedUser.DoesNotExist:
            return JsonResponse({"error": "User not found."}, status=404)
        except Exception as e:
            logger.error(f"Error editing user {user_id}: {e}", exc_info=True)
            return JsonResponse({"error": f"An error occurred: {str(e)}"}, status=500)
    
    # GET request to this URL isn't typical for AJAX forms but could render a form if needed.
    # For now, redirect or return error for GET.
    return JsonResponse({"error": "Invalid request method. Only POST is allowed for editing."}, status=405)

@csrf_exempt
def user_login(request):
    # Redirect to home page if already logged in
    if request.session.get("user_id"):
        return redirect("/home")

    if request.method == "POST":
        try:
            data = json.loads(request.body)
            username = data.get("email")
            entry_password = data.get("password")

            if not username or not entry_password:
                return JsonResponse(
                    {"error": "Username and password are required."}, status=400
                )

            user = InvitedUser.objects.filter(email=username).first()

            if user is None:
                return JsonResponse({"error": "Email does not exist."}, status=404)
            
            user_roles = [role.strip().lower() for role in user.role] if user.role else []
            request.session['user_roles'] = user_roles
            request.session['is_soft_admin'] = 'administrator' in user_roles or 'admin' in user_roles

            # ADD THIS CHECK
            if hasattr(user, "status") and user.status.lower() == "deactivated":
                return JsonResponse({"error": "Your account is deactivated. Please contact admin."}, status=403)


            stored_hashed_password = bytes(user.password)  # convert memoryview to bytes
            if bcrypt.checkpw(entry_password.encode(), stored_hashed_password):
                request.session["user_id"] = user.id
                request.session["user_email"] = user.email
                return JsonResponse({"message": "successful."}, status=200)

            return JsonResponse({"error": "Incorrect password."}, status=401)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON data."}, status=400)
        except Exception as e:
            return JsonResponse({"error": f"An error occurred: {str(e)}"}, status=500)

    return render(request, "user_login.html")


@login_required
def room_data_list(request):
    # Fetch room data from the database, including related RoomModel
    rooms = RoomData.objects.select_related('room_model_id').all()

    # Pass the room data to the template
    return render(request, "room_data_list.html", {"rooms": rooms})


@login_required
def inventory_list(request):
    # Check if we're coming from an edit (skip auto-updates)
    skip_updates = request.GET.get('skip_updates', 'false').lower() == 'true'
    
    if not skip_updates:
        # Update the shipped_to_hotel_quantity from warehouse_shipment data
        update_inventory_shipped_quantities()
        
        # Update the received_at_hotel_quantity and damaged_quantity_at_hotel from hotel_warehouse data
        update_inventory_hotel_warehouse_quantities()
        
        # Update the damaged_quantity from inventory_received data
        update_inventory_damaged_quantities()
        
        # Update the qty_received from inventory_received data
        update_inventory_received_quantities()
    
    # Fetch room data from the database
    inventory = Inventory.objects.all()
    # Pass the room data to the template
    return render(request, "inventory.html", {"inventory": inventory})


@login_required
def get_room_models(request):
    room_models = RoomModel.objects.all()
    room_model_list = [
        {"id": model.id, "name": model.room_model} for model in room_models
    ]
    return JsonResponse({"room_models": room_model_list})


@login_required
def add_room(request):
    if request.method == "POST":
        room_number = request.POST.get("room")
        floor = request.POST.get("floor")
        bed = request.POST.get("bed")
        bath_screen = request.POST.get("bath_screen")
        left_desk = request.POST.get("left_desk")
        right_desk = request.POST.get("right_desk")
        to_be_renovated = request.POST.get("to_be_renovated")
        room_model_id = request.POST.get("room_model")
        description = request.POST.get("description")

        # Assuming room_model is a ForeignKey
        room_model = RoomModel.objects.get(id=room_model_id)
        # Check if room number already exists
        if RoomData.objects.filter(room=room_number).exists():
            return JsonResponse({"error": "Room number already exists!"}, status=400)

        try:
            RoomData.objects.create(
                room=room_number,
                floor=floor,
                bed=bed,
                bath_screen=bath_screen,
                left_desk=left_desk,
                right_desk=right_desk,
                to_be_renovated=to_be_renovated,
                room_model=room_model.room_model,
                room_model_id=room_model,
                description=description,
            )
            print("RoomData id", RoomData.id)
            return JsonResponse({"success": "Room added successfully"})
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)


@login_required
def edit_room(request):
    if request.method == "POST":
        try:
            room_id = request.POST.get("room_id")
            room = get_object_or_404(RoomData, id=room_id)

            # Update room data
            room.room = request.POST.get("room")
            room.floor = request.POST.get("floor")
            room.bed = request.POST.get("bed")
            room.bath_screen = request.POST.get("bath_screen")
            room.left_desk = request.POST.get("left_desk")
            room.right_desk = request.POST.get("right_desk")
            room.to_be_renovated = request.POST.get("to_be_renovated")
            room.description = request.POST.get("description")

            # Update Room Model if given
            room_model_id = request.POST.get("room_model")
            if room_model_id:
                room_model = get_object_or_404(RoomModel, id=room_model_id)
                room.room_model = room_model.room_model
                room.room_model_id = room_model
                print("room = ", room_model)

            room.save()

            return JsonResponse({"success": "Room updated successfully!"})

        except RoomModel.DoesNotExist:
            return JsonResponse({"error": "Invalid room model!"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse({"error": "Invalid request"}, status=400)


@login_required
def delete_room(request):
    if request.method == "POST":
        room_id = request.POST.get("room_id")
        room = get_object_or_404(RoomData, id=room_id)

        try:
            room.delete()
            return JsonResponse({"success": "Room deleted successfully!"})
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse({"error": "Invalid request"}, status=400)

@login_required
def room_model_list(request):
    room_models = RoomModel.objects.all()
    return render(request, "room_model_list.html", {"room_models": room_models})


@login_required
def install_list(request):
    # Get all installation records, including related user data
    install_query = Installation.objects.select_related(
        'prework_checked_by',
        'post_work_checked_by',
        'product_arrived_at_floor_checked_by',
        'retouching_checked_by'
    ).all()

    # Get all distinct floor numbers from RoomData for the filter dropdown
    all_floors = RoomData.objects.order_by('floor').values_list('floor', flat=True).distinct()
    selected_floor = request.GET.get('floor')

    if selected_floor:
        try:
            selected_floor_int = int(selected_floor)
            # Filter installations by room numbers that are on the selected floor
            rooms_on_floor = RoomData.objects.filter(floor=selected_floor_int).values_list('room', flat=True)
            install_query = install_query.filter(room__in=rooms_on_floor)
        except ValueError:
            # Handle cases where floor parameter is not a valid integer, maybe show a message or ignore
            pass

    install = list(install_query) # Execute the query

    # Create a mapping of room numbers to their floors
    room_to_floor_map = {rd.room: rd.floor for rd in RoomData.objects.filter(room__in=[i.room for i in install])}

    # Convert the dates to a proper format and add floor information
    for installation in install:
        installation.floor = room_to_floor_map.get(installation.room) # Add floor to installation object
        if installation.day_install_began:
            installation.formatted_day_install_began = installation.day_install_began.strftime('%Y-%m-%d')
            installation.day_install_began = installation.day_install_began.strftime('%m-%d-%Y')
        if installation.day_install_complete:
            installation.formatted_day_install_complete = installation.day_install_complete.strftime('%Y-%m-%d')
            installation.day_install_complete = installation.day_install_complete.strftime('%m-%d-%Y')
        if installation.prework_check_on:
            installation.formatted_prework_check_on = installation.prework_check_on.strftime('%Y-%m-%d')
            installation.prework_check_on = installation.prework_check_on.strftime('%m-%d-%Y')
        if installation.post_work_check_on:
            installation.formatted_post_work_check_on = installation.post_work_check_on.strftime('%Y-%m-%d')
            installation.post_work_check_on = installation.post_work_check_on.strftime('%m-%d-%Y')
        if installation.retouching_check_on:
            installation.formatted_retouching_check_on = installation.retouching_check_on.strftime('%Y-%m-%d')
            installation.retouching_check_on = installation.retouching_check_on.strftime('%m-%d-%Y')
        if installation.product_arrived_at_floor_check_on:
            installation.formatted_product_arrived_at_floor_check_on = installation.product_arrived_at_floor_check_on.strftime('%Y-%m-%d')
            installation.product_arrived_at_floor_check_on = installation.product_arrived_at_floor_check_on.strftime('%m-%d-%Y')
    
    # Pass the modified install data and floor filter data to the template
    context = {
        "install": install,
        "all_floors": all_floors,
        "selected_floor": selected_floor
    }
    return render(request, "install.html", context)


@login_required
def product_data_list(request):
    product_data_list = ProductData.objects.all()
    return render(
        request, "product_data_list.html", {"product_data_list": product_data_list}
    )


@login_required
def schedule_list(request):
    schedule = Schedule.objects.all()
    # Convert the dates to a proper format for use in the template
    for one_schedule in schedule:
        if one_schedule.production_starts:
            one_schedule.formatted_production_starts = one_schedule.production_starts.strftime('%Y-%m-%d')
            one_schedule.production_starts = one_schedule.production_starts.strftime('%m-%d-%Y')
            
        if one_schedule.production_ends:
            one_schedule.formatted_production_ends = one_schedule.production_ends.strftime('%Y-%m-%d')
            one_schedule.production_ends = one_schedule.production_ends.strftime('%m-%d-%Y')
            
        if one_schedule.shipping_arrival:
            one_schedule.formatted_shipping_arrival = one_schedule.shipping_arrival.strftime('%Y-%m-%d')
            one_schedule.shipping_arrival = one_schedule.shipping_arrival.strftime('%m-%d-%Y')

        if one_schedule.shipping_depature:
            one_schedule.formatted_shipping_depature = one_schedule.shipping_depature.strftime('%Y-%m-%d')
            one_schedule.shipping_depature = one_schedule.shipping_depature.strftime('%m-%d-%Y')

        if one_schedule.custom_clearing_starts:
            one_schedule.formatted_custom_clearing_starts = one_schedule.custom_clearing_starts.strftime('%Y-%m-%d')
            one_schedule.custom_clearing_starts = one_schedule.custom_clearing_starts.strftime('%m-%d-%Y')

        if one_schedule.custom_clearing_ends:
            one_schedule.formatted_custom_clearing_ends = one_schedule.custom_clearing_ends.strftime('%Y-%m-%d')
            one_schedule.custom_clearing_ends = one_schedule.custom_clearing_ends.strftime('%m-%d-%Y')

        if one_schedule.arrive_on_site:
            one_schedule.formatted_arrive_on_site = one_schedule.arrive_on_site.strftime('%Y-%m-%d')
            one_schedule.arrive_on_site = one_schedule.arrive_on_site.strftime('%m-%d-%Y')

        if one_schedule.pre_work_starts:
            one_schedule.formatted_pre_work_starts = one_schedule.pre_work_starts.strftime('%Y-%m-%d')
            one_schedule.pre_work_starts = one_schedule.pre_work_starts.strftime('%m-%d-%Y')

        if one_schedule.pre_work_ends:
            one_schedule.formatted_pre_work_ends = one_schedule.pre_work_ends.strftime('%Y-%m-%d')
            one_schedule.pre_work_ends = one_schedule.pre_work_ends.strftime('%m-%d-%Y')

        if one_schedule.post_work_starts:
            one_schedule.formatted_post_work_starts = one_schedule.post_work_starts.strftime('%Y-%m-%d')
            one_schedule.post_work_starts = one_schedule.post_work_starts.strftime('%m-%d-%Y')

        if one_schedule.post_work_ends:
            one_schedule.formatted_post_work_ends = one_schedule.post_work_ends.strftime('%Y-%m-%d')
            one_schedule.post_work_ends = one_schedule.post_work_ends.strftime('%m-%d-%Y')

        if one_schedule.install_starts:
            one_schedule.formatted_install_starts = one_schedule.install_starts.strftime('%Y-%m-%d')
            one_schedule.install_starts = one_schedule.install_starts.strftime('%m-%d-%Y')

        if one_schedule.install_ends:
            one_schedule.formatted_install_ends = one_schedule.install_ends.strftime('%Y-%m-%d')
            one_schedule.install_ends = one_schedule.install_ends.strftime('%m-%d-%Y')

        if one_schedule.floor_opens:
            one_schedule.formatted_floor_opens = one_schedule.floor_opens.strftime('%Y-%m-%d')
            one_schedule.floor_opens = one_schedule.floor_opens.strftime('%m-%d-%Y')

        if one_schedule.floor_closes:
            one_schedule.formatted_floor_closes = one_schedule.floor_closes.strftime('%Y-%m-%d')
            one_schedule.floor_closes = one_schedule.floor_closes.strftime('%m-%d-%Y')

        if one_schedule.floor_completed:
            one_schedule.formatted_floor_completed = one_schedule.floor_completed.strftime('%Y-%m-%d')
            one_schedule.floor_completed = one_schedule.floor_completed.strftime('%m-%d-%Y')

    return render(request, "schedule.html", {"schedule": schedule})


@login_required
def save_room_model(request):
    print("ssssss")
    if request.method == "POST":
        model_id = request.POST.get("model_id")
        name = request.POST.get("name").strip()

        if not name:
            return JsonResponse({"error": "Model name is required"}, status=400)

        # Check for duplicate name (case-insensitive)
        existing_model = RoomModel.objects.filter(room_model__iexact=name)
        if model_id:
            existing_model = existing_model.exclude(id=model_id)

        if existing_model.exists():
            return JsonResponse(
                {"error": "A room model with this name already exists."}, status=400
            )

        if model_id:
            try:
                model = RoomModel.objects.get(id=model_id)
                model.room_model = name
                model.save()
                return JsonResponse({"success": "Room model updated successfully"})
            except RoomModel.DoesNotExist:
                return JsonResponse({"error": "Room model not found"}, status=404)
        else:
            RoomModel.objects.create(room_model=name)
            return JsonResponse({"success": "Room model created successfully"})

    return JsonResponse({"error": "Invalid request"}, status=400)


@login_required
def save_inventory(request):
    from django.db import connection
    
    if request.method == 'POST':
        inventory_id = request.POST.get('inventory_id')
        item = request.POST.get('item', '').strip()
        client_id = request.POST.get('client_id', '').strip()

        if not item:
            return JsonResponse({"error": "Item name is required"}, status=400)

        # Convert empty strings to 0 for numeric fields
        try:
            qty_ordered = request.POST.get('qty_ordered')
            qty_ordered = int(qty_ordered) if qty_ordered and qty_ordered.strip() else 0
            
            quantity_shipped = request.POST.get('quantity_shipped')
            quantity_shipped = int(quantity_shipped) if quantity_shipped and quantity_shipped.strip() else 0
            
            qty_received = request.POST.get('qty_received')
            qty_received = int(qty_received) if qty_received and qty_received.strip() else 0
            
            damaged_quantity = request.POST.get('damaged_quantity')
            damaged_quantity = int(damaged_quantity) if damaged_quantity and damaged_quantity.strip() else 0
            
            quantity_available = request.POST.get('quantity_available')
            quantity_available = int(quantity_available) if quantity_available and quantity_available.strip() else 0
            
            shipped_to_hotel_quantity = request.POST.get('shipped_to_hotel_quantity')
            shipped_to_hotel_quantity = int(shipped_to_hotel_quantity) if shipped_to_hotel_quantity and shipped_to_hotel_quantity.strip() else 0
            
            received_at_hotel_quantity = request.POST.get('received_at_hotel_quantity')
            received_at_hotel_quantity = int(received_at_hotel_quantity) if received_at_hotel_quantity and received_at_hotel_quantity.strip() else 0
            
            damaged_quantity_at_hotel = request.POST.get('damaged_quantity_at_hotel')
            damaged_quantity_at_hotel = int(damaged_quantity_at_hotel) if damaged_quantity_at_hotel and damaged_quantity_at_hotel.strip() else 0
            
            hotel_warehouse_quantity = request.POST.get('hotel_warehouse_quantity')
            hotel_warehouse_quantity = int(hotel_warehouse_quantity) if hotel_warehouse_quantity and hotel_warehouse_quantity.strip() else 0
            
            floor_quantity = request.POST.get('floor_quantity')
            floor_quantity = int(floor_quantity) if floor_quantity and floor_quantity.strip() else 0
            
            quantity_installed = request.POST.get('quantity_installed')
            quantity_installed = int(quantity_installed) if quantity_installed and quantity_installed.strip() else 0
        except ValueError:
            return JsonResponse({"error": "Quantities must be integers"}, status=400)

        # Check for duplicates
        existing = Inventory.objects.filter(item__iexact=item, client_id=client_id)
        if inventory_id:
            existing = existing.exclude(id=inventory_id)

        if existing.exists():
            return JsonResponse(
                {"error": "This item already exists for the given client."}, status=400
            )

        try:
            # If inventory_id is provided, update the existing record
            if inventory_id:
                # Use direct SQL update to bypass signal handlers that might override our changes
                with connection.cursor() as cursor:
                    cursor.execute("""
                        UPDATE inventory 
                        SET qty_ordered = %s,
                            quantity_shipped = %s,
                            qty_received = %s,
                            damaged_quantity = %s,
                            quantity_available = %s,
                            shipped_to_hotel_quantity = %s,
                            received_at_hotel_quantity = %s,
                            damaged_quantity_at_hotel = %s,
                            hotel_warehouse_quantity = %s,
                            floor_quantity = %s,
                            quantity_installed = %s
                        WHERE id = %s
                    """, [
                        qty_ordered,
                        quantity_shipped,
                        qty_received,
                        damaged_quantity,
                        quantity_available,
                        shipped_to_hotel_quantity,
                        received_at_hotel_quantity,
                        damaged_quantity_at_hotel,
                        hotel_warehouse_quantity,
                        floor_quantity,
                        quantity_installed,
                        inventory_id
                                        ])
                return JsonResponse({'success': True, 'skip_updates': True})
            else:
                # Create a new record
                Inventory.objects.create(
                    item=item,
                    client_id=client_id,
                    qty_ordered=qty_ordered,
                    quantity_shipped=quantity_shipped,
                    qty_received=qty_received,
                    damaged_quantity=damaged_quantity,
                    quantity_available=quantity_available,
                    shipped_to_hotel_quantity=shipped_to_hotel_quantity,
                    received_at_hotel_quantity=received_at_hotel_quantity,
                    damaged_quantity_at_hotel=damaged_quantity_at_hotel,
                    hotel_warehouse_quantity=hotel_warehouse_quantity,
                    floor_quantity=floor_quantity,
                    quantity_installed=quantity_installed,
                )
                return JsonResponse({'success': True, 'skip_updates': True})
        except Inventory.DoesNotExist:
            return JsonResponse({"error": "Inventory item not found"}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    return JsonResponse({"error": "Invalid request"}, status=400)


@login_required
def delete_room_model(request):
    if request.method == "POST":
        model_id = request.POST.get("model_id")
        try:
            room_model = RoomModel.objects.get(id=model_id)
            room_model.delete()
            return JsonResponse({"success": "Room Model deleted."})
        except RoomModel.DoesNotExist:
            return JsonResponse({"error": "Room Model not found."})
    return JsonResponse({"error": "Invalid request."})


@login_required
def delete_inventory(request):
    if request.method == "POST":
        model_id = request.POST.get("model_id")
        try:
            room_model = Inventory.objects.get(id=model_id)
            room_model.delete()
            return JsonResponse({"success": "Room Model deleted."})
        except Inventory.DoesNotExist:
            return JsonResponse({"error": "Room Model not found."})
    return JsonResponse({"error": "Invalid request."})


@login_required
def delete_schedule(request):
    if request.method == "POST":
        model_id = request.POST.get("model_id")
        try:
            schedule = Schedule.objects.get(id=model_id)
            schedule.delete()
            return JsonResponse({"success": "Room Model deleted."})
        except Inventory.DoesNotExist:
            return JsonResponse({"error": "Room Model not found."})
    return JsonResponse({"error": "Invalid request."})


@login_required
def delete_products_data(request):
    if request.method == "POST":
        model_id = request.POST.get("model_id")
        try:
            room_model = ProductData.objects.get(id=model_id)
            room_model.delete()
            return JsonResponse({"success": "Room Model deleted."})
        except ProductData.DoesNotExist:
            return JsonResponse({"error": "Room Model not found."})
    return JsonResponse({"error": "Invalid request."})

def _get_installation_checklist_data(room_number, installation_id=None, user_for_prefill=None):
    """
    Helper function to get installation checklist items and saved data.
    Can be used by both frontend and admin views.
    If installation_id is provided, it fetches data for that specific installation.
    Otherwise, it behaves like the original get_room_type for a given room_number.
    """
    try:
        room_data = RoomData.objects.select_related('room_model_id').get(room=room_number)
        room_type = room_data.room_model_id.room_model if room_data.room_model_id else ""
        room_model_instance = room_data.room_model_id

        if not room_model_instance:
            return {"success": False, "message": "Room model not found for this room."}

        # Determine the Installation instance
        if installation_id:
            installation_data = get_object_or_404(Installation, id=installation_id, room=room_number)
        else: # Original behavior for frontend form if no specific installation_id
            installation_data, _ = Installation.objects.get_or_create(
                room=room_data.room, # Use room_data.room (integer)
                defaults={ # Sensible defaults if creating
                    'prework': "NO",
                    'product_arrived_at_floor': "NO",
                    'retouching': "NO",
                    'post_work': "NO",
                }
            )
            # If created, it won't have an ID until saved. If fetched, it has one.
            # If it was just created, its ID might be None until a subsequent save by the form.
            # This is fine, as InstallDetail items will be created linked to this potential new installation.


        # Fetch products associated with the room model
        product_room_models = ProductRoomModel.objects.filter(
            room_model_id=room_model_instance.id
        ).select_related('product_id')

        saved_items = []
        check_items = [] # This will be the final list of all items (installation + details)

        # Fetch existing InstallDetail items for this installation
        # Ensure installation_data.id is valid before querying
        existing_install_details = []
        if installation_data and installation_data.id:
            existing_install_details = InstallDetail.objects.filter(
                installation_id=installation_data.id,
                room_id=room_data.id
            ).select_related("product_id", "installed_by")

        # Create a map of existing install details for quick lookup
        existing_details_map = {
            detail.product_id_id: detail for detail in existing_install_details
        }

        install_details_to_create_or_update = []

        for prm in product_room_models:
            product = prm.product_id
                # Get floor_quantity from Inventory
            inventory = Inventory.objects.filter(client_id__iexact=product.client_id).first()
            floor_quantity = inventory.floor_quantity if inventory else 0

            install_detail_item = existing_details_map.get(product.id)

            if not install_detail_item and installation_data and installation_data.id : # Only create if an installation record exists
                # This product is in the room model but not yet in InstallDetail for this installation
                install_detail_item = InstallDetail(
                    installation_id=installation_data.id, # Link to existing/created Installation
                    product_id=product,
                    room_id=room_data,
                    room_model_id=room_model_instance,
                    product_name=product.description or product.item, # Ensure product_name is set
                    status="NO" # Default status
                )
                # We can't bulk_create and then get IDs immediately if some items are new and installation_data was just created (no ID yet)
                # Instead, we'll prepare them. If installation_data has an ID, we save.
                # This part is tricky if installation_data was just created and doesn't have an ID.
                # For admin edit, installation_data.id will always exist.
                # For frontend, if InstallDetail items are crucial *before* first save, this needs care.
                # Assuming for now that if InstallDetail are created, installation_data.id is valid.
                if installation_data.id: # Ensure main installation record has an ID
                    install_detail_item.save() # Save to get an install_id (PK)
                    existing_details_map[product.id] = install_detail_item # Add to map
                else:
                    # If installation record is new (no ID), these won't be saved yet.
                    # This scenario is more for the initial GET in the frontend form.
                    # They will be properly created during the POST save.
                    pass # Defer creation to the POST if main installation is new.

            # print(f"install_detail_item: {install_detail_item}")
            # Prepare data for saved_items and check_items
            if install_detail_item: # If it exists or was just saved
                saved_items.append({
                    "install_id": install_detail_item.install_id,
                    "product_id": product.id,
                    "product_name": install_detail_item.product_id.description,
                    "room_id": room_data.id,
                    "room_model_id": room_model_instance.id,
                    "product_room_model_id": prm.id, # ID of the ProductRoomModel mapping
                    "installed_by": install_detail_item.installed_by.name if install_detail_item.installed_by else None,
                    "installed_on": install_detail_item.installed_on.isoformat() if install_detail_item.installed_on else None,
                    "status": install_detail_item.status,
                    "product_client_id": product.client_id,
                    "product_image": product.image.url if product.image else None,
                })
                check_items.append({
                    "id": install_detail_item.install_id, # This is InstallDetail PK
                    "label": f"({product.client_id or 'N/A'}) - {install_detail_item.product_id.description}",
                    "type": "detail",
                    "status": install_detail_item.status,
                    "checked_by": install_detail_item.installed_by.name if install_detail_item.installed_by else None,
                    "check_on": localtime(install_detail_item.installed_on).isoformat() if install_detail_item.installed_on else None,
                    "quantity_needed_per_room": prm.quantity,
                    "floor_quantity": floor_quantity, 
                    "image_url": product.image.url if product.image else None, 
                })
            elif not installation_data.id : # Product from room model, but main installation record is new (no ID yet)
                 # This is for the initial rendering of the frontend form for a NEW installation
                 # Create temporary placeholder items
                check_items.append({
                    "id": f"newproduct_{product.id}", # Temporary ID for unsaved items
                    "label": f"({product.client_id or 'N/A'}) - {install_detail_item.product_id.description}",
                    "type": "detail",
                    "status": "NO", # Default
                    "checked_by": None,
                    "check_on": None,
                    "quantity_needed_per_room": prm.quantity,
                    "floor_quantity": floor_quantity, 
                })


        # Add Installation-level static checklist items
        if installation_data: # This will always be true due to get_or_create or get_object_or_404
            static_install_items = [
                {
                    "id": 0, "label": "Pre-Work completed.",
                    "checked_by": installation_data.prework_checked_by.name if installation_data.prework_checked_by else None,
                    "check_on": localtime(installation_data.prework_check_on).isoformat() if installation_data.prework_check_on else None,
                    "status": installation_data.prework, "type": "installation"
                },
                {
                    "id": 1, "label": "The product arrived at the floor.",
                    "checked_by": installation_data.product_arrived_at_floor_checked_by.name if installation_data.product_arrived_at_floor_checked_by else None,
                    "check_on": localtime(installation_data.product_arrived_at_floor_check_on).isoformat() if installation_data.product_arrived_at_floor_check_on else None,
                    "status": installation_data.product_arrived_at_floor, "type": "installation"
                },
                {
                    "id": 12, "label": "Retouching.",
                    "checked_by": installation_data.retouching_checked_by.name if installation_data.retouching_checked_by else None,
                    "check_on": localtime(installation_data.retouching_check_on).isoformat() if installation_data.retouching_check_on else None,
                    "status": installation_data.retouching, "type": "installation"
                },
                {
                    "id": 13, "label": "Post Work.",
                    "checked_by": installation_data.post_work_checked_by.name if installation_data.post_work_checked_by else None,
                    "check_on": localtime(installation_data.post_work_check_on).isoformat() if installation_data.post_work_check_on else None,
                    "status": installation_data.post_work, "type": "installation"
                },
            ]
            check_items.extend(static_install_items)
        
        # Sort check_items: installation steps first, then details.
        # Within installation steps, sort by specific IDs (0,1 then 12,13)
        # Within detail steps, sort by label perhaps, or product ID.
        def sort_key(item):
            if item['type'] == 'installation':
                if item['id'] == 0: return (0, 0)
                if item['id'] == 1: return (0, 1)
                if item['id'] == 12: return (0, 12)
                if item['id'] == 13: return (0, 13)
                return (0, item['id']) # Should not happen with current static IDs
            else: # type == 'detail'
                # Ensure consistent sorting for details, e.g., by label
                return (1, item['label'])


        check_items = sorted(check_items, key=sort_key)

        return {
            "success": True,
            "room_type": room_type,
            "check_items": check_items, # This now contains both installation and detail types
            "saved_items": saved_items, # This primarily contains formatted InstallDetail data
            "installation_id": installation_data.id if installation_data else None,
            "room_id_for_installation": room_data.id, # Pass room_data.id for clarity
        }

    except RoomData.DoesNotExist:
        return {"success": False, "message": "Room not found"}
    except RoomModel.DoesNotExist:
        return {"success": False, "message": "Room model not found for this room"}
    except Installation.DoesNotExist:
        return {"success": False, "message": "Installation record not found"}
    except Exception as e:
        logger.error(f"Error in _get_installation_checklist_data for room {room_number}, install_id {installation_id}: {e}", exc_info=True)
        return {"success": False, "message": f"An unexpected error occurred: {str(e)}"}


# Define parse_date at the module level
def parse_date(date_str):
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date() if date_str and date_str.strip() else None
    except ValueError:
        try:
            # Fallback for datetime strings if time is included
            return datetime.strptime(date_str.strip(), "%Y-%m-%dT%H:%M:%S.%fZ").date() if date_str and date_str.strip() else None
        except ValueError:
            try:
                return datetime.strptime(date_str.strip(), "%Y-%m-%d %H:%M:%S").date() if date_str and date_str.strip() else None
            except ValueError:
                logger.warning(f"Could not parse date string: {date_str}")
                return None

@session_login_required
def get_room_type(request):
    room_number_str = request.GET.get("room_number")
    if not room_number_str:
        return JsonResponse({"success": False, "message": "Room number not provided."}, status=400)
    
    try:
        # Attempt to convert room_number to integer if your RoomData.room is an IntegerField
        room_number = int(room_number_str)
    except ValueError:
        return JsonResponse({"success": False, "message": "Invalid room number format."}, status=400)

    # Call the helper function
    # For the frontend form, we don't pass a specific installation_id initially,
    # the helper will do get_or_create for the Installation object.
    data = _get_installation_checklist_data(room_number=room_number)
    return JsonResponse(data)


def _save_installation_data(request_post_data, user_instance, room_number_str, installation_id_str=None):
    """
    Helper function to save installation data.
    Used by both frontend installation_form and admin_save_installation_details.
    `room_number_str` is used to fetch RoomData if installation_id is not provided or to verify.
    `installation_id_str` is explicitly passed for admin edits.
    """
    print(request_post_data)
            # Now use product_id, step_checked, etc.

    try:
        if not room_number_str:
            return {"success": False, "message": "Room number is required."}
        
        try:
            room_number_int = int(room_number_str)
            room_instance = get_object_or_404(RoomData, room=room_number_int)
        except ValueError:
            return {"success": False, "message": "Invalid room number format."}
        except RoomData.DoesNotExist:
            return {"success": False, "message": f"Room {room_number_str} not found."}

        # Determine the Installation instance
        if installation_id_str: # Admin edit or frontend if it was an existing installation
            installation_id = int(installation_id_str)
            installation_instance = get_object_or_404(Installation, id=installation_id, room=room_instance.room)
        else: # Frontend creating a new one or updating based on room number only
            installation_instance, created = Installation.objects.get_or_create(
                room=room_instance.room, # Ensure this matches the field type (e.g. room number if integer)
                defaults={'prework': "NO", 'product_arrived_at_floor':"NO", 'retouching':"NO", 'post_work':"NO"}
            )
            if created:
                logger.info(f"Created new Installation record for room {room_instance.room}")
        
        # Process main installation steps
        main_steps_map = {
            0: ('prework', 'prework_check_on', 'prework_checked_by'),
            1: ('product_arrived_at_floor', 'product_arrived_at_floor_check_on', 'product_arrived_at_floor_checked_by'),
            12: ('retouching', 'retouching_check_on', 'retouching_checked_by'),
            13: ('post_work', 'post_work_check_on', 'post_work_checked_by'),
        }

        for step_id_int, fields in main_steps_map.items():
            status_attr, date_attr, user_attr = fields
            checkbox_key = f"step_installation_{step_id_int}"
            form_date_key = f"date_installation_{step_id_int}"
            form_user_key = f"checked_by_installation_{step_id_int}"

            old_status_val = getattr(installation_instance, status_attr) == "YES"
            old_date_val = getattr(installation_instance, date_attr)
            old_user_val = getattr(installation_instance, user_attr)

            is_checked_in_form = request_post_data.get(checkbox_key) == "on"
            form_date_str = request_post_data.get(form_date_key)
            form_user_name = request_post_data.get(form_user_key, "").strip()

            if is_checked_in_form:
                setattr(installation_instance, status_attr, "YES")
                
                parsed_form_date = parse_date(form_date_str)
                if parsed_form_date:
                    setattr(installation_instance, date_attr, parsed_form_date)
                elif not old_status_val: # Newly checked and no specific date in form
                    setattr(installation_instance, date_attr, now().date())
                else: # Was already checked, form date field empty/invalid, preserve old
                    setattr(installation_instance, date_attr, old_date_val)

                # User assignment for checked item
                if not old_status_val: # Newly checked
                    setattr(installation_instance, user_attr, user_instance)
                else: # Was already checked
                    if form_user_name == user_instance.name: # JS set to current user, or admin typed their name
                        setattr(installation_instance, user_attr, user_instance)
                    elif not form_user_name and old_user_val: # User field cleared for already checked item
                        setattr(installation_instance, user_attr, old_user_val) # Preserve old user
                    elif old_user_val and form_user_name == old_user_val.name: # Name in form matches old user
                        setattr(installation_instance, user_attr, old_user_val) # Preserve old user
                    elif form_user_name: # Admin typed some other name or JS populated it (and it's not old user)
                        # Default to current user if form has a name not matching old user, 
                        # implying interaction or JS update.
                        setattr(installation_instance, user_attr, user_instance)
                    else: # Fallback, preserve old user if form name is empty and didn't match current user above
                         setattr(installation_instance, user_attr, old_user_val)
            else: # Not checked in form
                setattr(installation_instance, status_attr, "NO")
                setattr(installation_instance, date_attr, None)
                setattr(installation_instance, user_attr, None)
        
        installation_instance.save() # Save main installation steps

        # Process InstallDetail items
        # First, get all existing InstallDetail items for this installation
        existing_details = InstallDetail.objects.filter(installation=installation_instance)
        existing_detail_ids = {str(detail.pk) for detail in existing_details}
        
        # Get the list of all detail IDs that were rendered in the form
        # This is needed because the form includes hidden fields for all checkboxes
        rendered_detail_ids = set()
        for key in request_post_data:
            if key.startswith("step_detail_") and not key.startswith("step_detail_newproduct_"):
                detail_id = key.split("step_detail_")[1]
                rendered_detail_ids.add(detail_id)
        
        # Track which details were processed in this submission
        processed_detail_ids = set()
        
        for key in request_post_data:
            if key.startswith("step_detail_"):
                try:
                    step_id_str = key.split("_")[2]
                    form_date_key = f"date_detail_{step_id_str}"
                    form_user_key = f"checked_by_detail_{step_id_str}"

                    is_checked_in_form = request_post_data.get(key) == "on"
                    form_date_str = request_post_data.get(form_date_key)
                    form_user_name = request_post_data.get(form_user_key, "").strip()
                    
                    # Add to processed IDs if it's an existing detail
                    if not step_id_str.startswith("newproduct_"):
                        processed_detail_ids.add(step_id_str)
                    
                    install_detail_item = None
                    created_new_detail = False

                    if step_id_str.startswith("newproduct_"):
                        if not is_checked_in_form: continue

                        product_id_for_new = int(step_id_str.split("_")[1])
                        product_instance = get_object_or_404(ProductData, id=product_id_for_new)
                        room_model_instance = room_instance.room_model_id
                        print('instance .........',product_instance.item)
                        install_detail_item, created = InstallDetail.objects.get_or_create(
                            installation=installation_instance,
                            product_id=product_instance,
                            room_id=room_instance,
                            defaults={
                                'room_model_id': room_model_instance,
                                'product_name': product_instance.description or product_instance.item,
                                'status': "YES",
                                'installed_on': parse_date(form_date_str) or now().date(),
                                'installed_by': user_instance
                            }
                        )
                        created_new_detail = created
                        if not created: # Already existed, treat as normal update path below
                            pass 
                        else: # Newly created and defaults set, skip further processing for this item in this loop iteration
                            continue # Already saved with correct initial values

                    else: # Existing InstallDetail item
                        detail_pk = int(step_id_str)
                        install_detail_item = get_object_or_404(InstallDetail, pk=detail_pk)
                        if install_detail_item.installation_id != installation_instance.id:
                            logger.warning(f"Data mismatch: InstallDetail {detail_pk}...")
                            continue
                    
                    # Common logic for existing or just-fetched-not-newly-created items
                    old_status_val = install_detail_item.status == "YES"
                    old_date_val = install_detail_item.installed_on
                    old_user_val = install_detail_item.installed_by

                    if is_checked_in_form:
                        install_detail_item.status = "YES"
                        parsed_form_date = parse_date(form_date_str)
                        if parsed_form_date:
                            install_detail_item.installed_on = parsed_form_date
                        elif not old_status_val: # Newly checked and no date in form
                            install_detail_item.installed_on = now().date()
                        else: # Was already checked, form date field empty/invalid
                            install_detail_item.installed_on = old_date_val
                        
                        # User assignment for checked detail item
                        if not old_status_val: # Newly checked
                            install_detail_item.installed_by = user_instance
                        else: # Was already checked
                            if form_user_name == user_instance.name:
                                install_detail_item.installed_by = user_instance
                            elif not form_user_name and old_user_val:
                                install_detail_item.installed_by = old_user_val
                            elif old_user_val and form_user_name == old_user_val.name:
                                install_detail_item.installed_by = old_user_val
                            elif form_user_name: # Admin typed some other name or JS populated it
                                install_detail_item.installed_by = user_instance # Default to current saver
                            else:
                                install_detail_item.installed_by = old_user_val
                    else: # Not checked in form
                        install_detail_item.status = "NO"
                        install_detail_item.installed_on = None
                        install_detail_item.installed_by = None
                    
                    install_detail_item.save()
                        
                except InstallDetail.DoesNotExist:
                    logger.error(f"InstallDetail with ID {step_id_str} not found...")

        # --- Automatic setting of day_install_began, install status, and day_install_complete ---
        installation_data_changed_by_automation = False

        # 1. Automatic setting of day_install_began
        # This relies on installation_instance.prework and installation_instance.prework_check_on
        # having been updated earlier in this function from the form data.
        if installation_instance.prework == "YES" and installation_instance.prework_check_on:
            # prework_check_on is DateTimeField, day_install_began is DateTimeField
            if installation_instance.day_install_began != installation_instance.prework_check_on:
                installation_instance.day_install_began = installation_instance.prework_check_on
                installation_data_changed_by_automation = True
        elif installation_instance.prework == "NO": # If prework is not YES or becomes NO
            if installation_instance.day_install_began is not None:
                installation_instance.day_install_began = None
                installation_data_changed_by_automation = True
        
        # 2. Automatic setting of install status and day_install_complete
        all_details_for_install = InstallDetail.objects.filter(installation=installation_instance)
        all_required_details_completed = False # Default to false
        latest_detail_completion_date = None

        if all_details_for_install.exists():
            all_required_details_completed = True # Assume true until a non-completed item is found
            for detail in all_details_for_install:
                if detail.status != "YES":
                    all_required_details_completed = False
                    latest_detail_completion_date = None # Reset if any item is not complete
                    break
                if detail.installed_on: # This is DateTimeField
                    if latest_detail_completion_date is None or detail.installed_on > latest_detail_completion_date:
                        latest_detail_completion_date = detail.installed_on
            if not all_required_details_completed: # if loop broke, ensure latest_date is None
                 latest_detail_completion_date = None
        else:
            # No InstallDetail items found for this installation.
            # "when all the dynamic products are marked done" - if no products, this condition is not met for "YES".
            all_required_details_completed = False 
            latest_detail_completion_date = None


        current_install_status = installation_instance.install
        current_install_complete_date = installation_instance.day_install_complete

        if all_required_details_completed and latest_detail_completion_date:
            # All details are completed and there's a valid completion date.
            if current_install_status != "YES":
                installation_instance.install = "YES"
                installation_data_changed_by_automation = True
            if current_install_complete_date != latest_detail_completion_date:
                installation_instance.day_install_complete = latest_detail_completion_date
                installation_data_changed_by_automation = True
        else: 
            # Not all details completed, or no details exist, or no latest completion date found (e.g. all products 'YES' but no dates)
            if current_install_status == "YES": # Only change if it was "YES"
                installation_instance.install = "NO" 
                installation_data_changed_by_automation = True
            if current_install_complete_date is not None:
                installation_instance.day_install_complete = None
                installation_data_changed_by_automation = True
        
        if installation_data_changed_by_automation:
            installation_instance.save()
            logger.info(f"Installation {installation_instance.id} updated by automation: day_install_began, install, day_install_complete.")
        
        # Handle details that were rendered in the form but weren't processed
        # These are the checkboxes that were rendered but weren't touched in this submission
        unprocessed_detail_ids = rendered_detail_ids - processed_detail_ids
        if unprocessed_detail_ids:
            logger.info(f"Found {len(unprocessed_detail_ids)} rendered but unprocessed details for installation {installation_instance.id}")
            # These details were rendered in the form but weren't processed, so we should leave them as they are
            # No need to update them as they weren't touched

        return {"success": True, "message": "Installation data saved successfully!"}

    except Installation.DoesNotExist:
        return {"success": False, "message": "Installation record not found."}
    except RoomData.DoesNotExist:
         return {"success": False, "message": f"Room {room_number_str} not found."}
    except Exception as e:
        logger.error(f"Error in _save_installation_data for room {room_number_str}, install_id {installation_id_str}: {e}", exc_info=True)
        return {"success": False, "message": f"An unexpected error occurred: {str(e)}"}


@session_login_required
def installation_form(request):
    if not request.session.get("user_id"): # Redundant due to decorator, but good practice
        messages.warning(request, "You must be logged in to access the form.")
        return redirect("user_login")
    
    invited_user_id = request.session.get("user_id")
    invited_user_instance = get_object_or_404(InvitedUser, id=invited_user_id)
    checked_product_ids = []

    # Track modified items (unchecked or newly checked)
    modified_details = {}
    for key, value in request.POST.items():
        if key.startswith("modified_detail_"):
            detail_id = key.split("modified_detail_")[1]
            modified_details[detail_id] = True
    
    # Process checked items
    for key, value in request.POST.items():
        if key.startswith("step_detail_") and value == "on":
            detail_id = key.split("step_detail_")[1]
            product_id = request.POST.get(f"product_id_detail_{detail_id}")
            
            # Check if this is a previous installation - don't update inventory again
            previous_install = request.POST.get(f"previous_install_detail_{detail_id}") == "true"
            if previous_install:
                # Skip previously installed items - they were already counted in inventory
                continue
                
            # Get required quantity for this product (from ProductRoomModel)
            install_detail = InstallDetail.objects.filter(install_id=detail_id).first()
            if install_detail:
                room_model_id = install_detail.room_model_id_id
                product_data_id = install_detail.product_id_id
                prm = ProductRoomModel.objects.filter(room_model_id=room_model_id, product_id=product_data_id).first()
                required_qty = prm.quantity if prm else 1
            else:
                required_qty = 1
                
            if product_id:
                try:
                    inventory_item = Inventory.objects.get(client_id=product_id)
                    
                    # Only increment install qty if not previously installed or not modified
                    # (a modification from unchecked to checked will add)
                    if not install_detail or install_detail.status != "YES" or modified_details.get(detail_id):
                        if inventory_item.floor_quantity is not None:
                            inventory_item.floor_quantity = max(0, inventory_item.floor_quantity - required_qty)
                        if inventory_item.quantity_installed is not None:
                            inventory_item.quantity_installed += required_qty
                        else:
                            inventory_item.quantity_installed = required_qty
                        inventory_item.save()
                        
                except Inventory.DoesNotExist:
                    print(f"Inventory item with product_id {product_id} does not exist.")
    
    # Handle items that were unchecked (previously YES but now unchecked)
    for key, value in request.POST.items():
        if key.startswith("step_detail_") and value == "off" and modified_details.get(key.split("step_detail_")[1]):
            detail_id = key.split("step_detail_")[1]
            
            # Only process if this was modified from checked to unchecked
            if not request.POST.get(f"current_session_{key.split('step_')[1]}") and modified_details.get(detail_id):
                install_detail = InstallDetail.objects.filter(install_id=detail_id).first()
                if install_detail and install_detail.status == "YES" and install_detail.product_id:
                    product_id = install_detail.product_id.client_id
                    
                    # Get required quantity to revert
                    room_model_id = install_detail.room_model_id_id
                    product_data_id = install_detail.product_id_id
                    prm = ProductRoomModel.objects.filter(room_model_id=room_model_id, product_id=product_data_id).first()
                    required_qty = prm.quantity if prm else 1
                    
                    try:
                        # Revert the inventory - add back to floor, remove from installed
                        inventory_item = Inventory.objects.get(client_id=product_id)
                        if inventory_item.floor_quantity is not None:
                            inventory_item.floor_quantity += required_qty
                        if inventory_item.quantity_installed is not None:
                            inventory_item.quantity_installed = max(0, inventory_item.quantity_installed - required_qty)
                        inventory_item.save()
                    except Inventory.DoesNotExist:
                        print(f"Inventory item with product_id {product_id} does not exist.")
    #             checked_product_ids.append(product_id)

    # # Now checked_product_ids contains ONLY product IDs that were ticked
    # print("Checked Product IDs:", checked_product_ids)
    # for product_id in checked_product_ids:
    #     try:
    #         inventory_item = Inventory.objects.get(item=product_id)
    #         inventory_item.quantity_installed += 1
    #         inventory_item.save()
    #     except Inventory.DoesNotExist:
    #         print(f"Inventory item with product_id {product_id} does not exist.")

    # Now use product_id, step_checked, etc.

    if request.method == "POST":
        room_number_str = request.POST.get("room_number")
        # For frontend, installation_id might not be explicitly in POST if it's a new installation.
        # The _save_installation_data helper will handle get_or_create for Installation.
        # If the form *does* pass an installation_id (e.g., from a hidden field after initial GET), it could be used.
        # For now, relying on room_number for get_or_create logic in the helper for frontend.

        # --- Begin: Inventory adjustment for unchecked products ---
        try:
            # Get the installation instance for this room
            room_instance = RoomData.objects.filter(room=room_number_str).first()
            if room_instance:
                installation_instance = Installation.objects.filter(room=room_instance.room).first()
                if installation_instance:
                    # Get all InstallDetail for this installation
                    all_details = InstallDetail.objects.filter(installation=installation_instance)
                    
                    # Get the list of all detail IDs that were rendered in the form
                    # This is needed because the form includes hidden fields for all checkboxes
                    rendered_detail_ids = set()
                    for key in request.POST:
                        if key.startswith("step_detail_") and not key.startswith("step_detail_newproduct_"):
                            detail_id = key.split("step_detail_")[1]
                            rendered_detail_ids.add(detail_id)
                    
                    for detail in all_details:
                        detail_id_str = str(detail.install_id)
                        key = f"step_detail_{detail_id_str}"
                        was_checked = detail.status == "YES"
                        
                        # Only process details that were rendered in the form
                        if detail_id_str not in rendered_detail_ids:
                            # This detail wasn't rendered in the form, so skip it
                            continue
                            
                        is_now_checked = request.POST.get(key) == "on"
                        # If it was checked before but now is unchecked
                        if was_checked and not is_now_checked:
                            # Get required quantity for this product (from ProductRoomModel)
                            prm = ProductRoomModel.objects.filter(room_model_id=detail.room_model_id_id, product_id=detail.product_id_id).first()
                            required_qty = prm.quantity if prm else 1
                            try:
                                inventory_item = Inventory.objects.get(client_id=detail.product_id.client_id)
                                # Only decrement if possible (avoid negative values)
                                if inventory_item.quantity_installed is not None and inventory_item.quantity_installed >= required_qty:
                                    inventory_item.quantity_installed -= required_qty
                                else:
                                    inventory_item.quantity_installed = max((inventory_item.quantity_installed or 0) - required_qty, 0)
                                # Increment floor_quantity back
                                inventory_item.floor_quantity = (inventory_item.floor_quantity or 0) + required_qty
                                inventory_item.save()
                            except Inventory.DoesNotExist:
                                pass
        except Exception as e:
            print(f"Error adjusting inventory for unchecked products: {e}")
        # --- End: Inventory adjustment for unchecked products ---

        result = _save_installation_data(request.POST, invited_user_instance, room_number_str)

        if result["success"]:
            messages.success(request, result["message"])
        else:
            messages.error(request, result["message"])
        return redirect("installation_form") # Redirect back to the form page

    # Fetch data for the "Previous Summary" table - SIMPLIFIED APPROACH
    previous_summaries = []
    
    try:
        # Print for debugging
        print("Fetching installation data for summary table...")
        
        # Get the installations with a simpler approach - limit to 50 for performance
        installations = Installation.objects.all().order_by('room')
        
        room_numbers = [inst.room for inst in installations]
        room_data_map = {
            rd.room: rd for rd in RoomData.objects.filter(room__in=room_numbers).select_related('room_model_id')
        }
        
        install_ids = [inst.id for inst in installations]
        details = InstallDetail.objects.filter(installation_id__in=install_ids)
        details_map = {}
        for detail in details:
            details_map.setdefault(detail.installation_id, []).append(detail)
        
        previous_summaries = []
        for installation in installations:
            room_data = room_data_map.get(installation.room)
            room_type = room_data.room_model_id.room_model if room_data and room_data.room_model_id else "Unknown"
            install_details = details_map.get(installation.id, [])
            installed_count = sum(1 for d in install_details if d.status == "YES")
            pending_count = sum(1 for d in install_details if d.status == "NO")
            summary_entry = {
                'room_number': installation.room,
                'room_type': room_type,
                'prework': "YES" if installation.prework == "YES" else "NO",
                'product_arrival': "YES" if installation.product_arrived_at_floor == "YES" else "NO",
                'retouching': "YES" if installation.retouching == "YES" else "NO",
                'product_installed': f"{installed_count} item(s)",
                'pending_products': f"{pending_count} item(s)",
            }
            previous_summaries.append(summary_entry)

        page = request.GET.get('page', 1)
        paginator = Paginator(previous_summaries, 50) # 50 summaries per page
        try:
            summaries_page = paginator.page(page)
        except PageNotAnInteger:
            summaries_page = paginator.page(1)
        except EmptyPage:
            summaries_page = paginator.page(paginator.num_pages)
        return render(request, "installation_form.html", {
            "invited_user": invited_user_instance, # Used by JS to prefill user name
            "previous_summaries": summaries_page,
             "is_paginated":summaries_page.has_other_pages(),
            "page_obj": summaries_page, # For pagination in the template
        })
              
              
            #    Data for the previous summaries table      
            # except Exception as inst_err:
            #     print(f"Error processing installation {getattr(installation, 'id', 'unknown')}: {inst_err}")
            #     # Skip this installation but continue processing others
        
        print(f"Successfully loaded {len(previous_summaries)} installation summaries")
        
    except Exception as e:
        print(f"Critical error fetching installation summaries: {e}")
        logger.error(f"Error fetching installation summaries: {e}", exc_info=True)
        # Add a basic summary entry to ensure the page renders
        previous_summaries = [
            {
                'room_number': 'Error',
                'room_type': 'Error loading data',
                'prework': 'N/A',
                'product_arrival': 'N/A',
                'retouching': 'N/A',
                'product_installed': 'Error',
                'pending_products': 'Error',
            }
        ]
    
    # Print summary for debugging
    print(f"Final previous_summaries count: {len(previous_summaries)}")
    
    # For GET request, the existing JS will call get_room_type to populate the form.
    return render(request, "installation_form.html", {
        "invited_user": invited_user_instance, # Used by JS to prefill user name
        "previous_summaries": previous_summaries # Data for the previous summaries table
    })

@session_login_required
def inventory_shipment(request):
    user_id = request.session.get("user_id")
    user_name = ""

    if user_id:
        try:
            user = InvitedUser.objects.get(id=user_id)
            user_name = user.name
        except InvitedUser.DoesNotExist:
            pass

    if request.method == "POST":
        try:
            # Get common form fields
            ship_date_str = request.POST.get("ship_date")
            expected_arrival_date_str = request.POST.get("expected_arrival_date")
            tracking_info = request.POST.get("tracking_info")
            
            # Check if this is an edit operation
            is_editing = request.POST.get("is_editing") == "1"
            editing_container_id = request.POST.get("editing_container_id", "").strip()
            
            # Debug logging
            print(f"Form submission - is_editing value: '{request.POST.get('is_editing')}'")
            print(f"Form submission - is_editing: {is_editing}, editing_container_id: '{editing_container_id}'")
            print(f"Current tracking_info (Container ID): '{tracking_info}'")
            
            # Parse dates
            if ship_date_str:
                ship_date = make_aware(datetime.strptime(ship_date_str, "%Y-%m-%d"))
            else:
                ship_date = None
            
            if expected_arrival_date_str:
                expected_arrival_date = make_aware(datetime.strptime(expected_arrival_date_str, "%Y-%m-%d"))
            else:
                expected_arrival_date = None
                
            # Get multiple items data
            client_items = request.POST.getlist("client_items")
            product_names = request.POST.getlist("product_names")
            suppliers = request.POST.getlist("suppliers")
            quantities = request.POST.getlist("quantities")
            
            # Debug logging
            print(f"Items to process: {len(client_items)}")
            print(f"Client items: {client_items}")
            print(f"Suppliers: {suppliers}")
            
            # Check if we have items to process
            if not client_items:
                messages.error(request, "No items added to shipment.")
                return redirect("inventory_shipment")
            
            # Check if this container ID already exists (regardless of edit flag)
            container_exists = False
            if tracking_info:
                existing_count = Shipping.objects.filter(bol=tracking_info).count()
                if existing_count > 0:
                    container_exists = True
                    print(f"Container ID '{tracking_info}' already exists with {existing_count} items")
                    
                    # If not explicitly in edit mode, set it to edit mode
                    if not is_editing:
                        is_editing = True
                        editing_container_id = tracking_info
                        print(f"Setting to EDIT MODE because container ID '{tracking_info}' already exists")
            
            # If editing, delete all previous items with the same container_id
            if is_editing and editing_container_id:
                print(f"EDIT MODE DETECTED - Will replace items in container: {editing_container_id}")
                
                # Get count of deleted items for message
                deleted_count = Shipping.objects.filter(bol=editing_container_id).count()
                
                # Debug logging
                print(f"Deleting {deleted_count} items with container ID: {editing_container_id}")
                
                # Delete previous entries
                Shipping.objects.filter(bol=editing_container_id).delete()
                
                # Use the original container ID when editing
                # tracking_info = editing_container_id
                
                print(f"After delete - using container ID: {tracking_info}")
                
                if container_exists:
                    messages.info(request, f"Found existing container ID '{tracking_info}'. Updated with new items.")
                else:
                    messages.info(request, f"Deleted {deleted_count} previous items from this container.")
            elif container_exists:
                # This should not happen with our new logic, but just in case:
                messages.warning(request, f"Container ID '{tracking_info}' already exists. Creating duplicate container.")
            else:
                print(f"NEW SUBMISSION MODE - Creating new container with ID: {tracking_info}")
            
            # Process each item
            for i in range(len(client_items)):
                client_item = client_items[i]
                supplier = suppliers[i] if i < len(suppliers) else ""
                qty_shipped = int(quantities[i]) if i < len(quantities) and quantities[i] else 0
                
                # Save the shipping entry
                Shipping.objects.create(
                    client_id=client_item,
                    item=client_item,
                    ship_date=ship_date,
                    ship_qty=qty_shipped,
                    supplier=supplier,
                    bol=tracking_info,
                    checked_by=user,
                    expected_arrival_date=expected_arrival_date
                )
                
                # Update Inventory
                inventory = Inventory.objects.filter(
                    client_id__iexact=client_item,
                    item__iexact=client_item
                ).first()
                
                if inventory:
                    inventory.qty_ordered = (inventory.qty_ordered or 0) + qty_shipped
                    inventory.save()
            
            if is_editing:
                messages.success(request, f"Updated shipment with {len(client_items)} items successfully!")
            else:
                messages.success(request, f"New shipment with {len(client_items)} items submitted and inventory updated!")
            
            # After successful shipment creation/update, update inventory quantities
            update_inventory_shipped_quantities()
            
            # Return redirect instead of JSON response to avoid displaying JSON to user
            return redirect('inventory_shipment')

        except Exception as e:
            print("error ::", e)
            messages.error(request, f"Error submitting shipment: {str(e)}")

    # Get previous submissions (grouped by container ID/BOL)
    previous_submissions = []
    try:
        # Get unique container IDs
        containers = Shipping.objects.values('bol').distinct().order_by('-ship_date')
        
        for container in containers:
            if not container['bol']:  # Skip empty BOLs
                continue
                
            # Get all items for this container
            items = Shipping.objects.filter(bol=container['bol']).order_by('-ship_date')
            if not items:
                continue
                
            first_item = items.first()  # Get the first item to extract common data
            
            # Format the dates for display
            ship_date_display = first_item.ship_date.strftime('%Y-%m-%d') if first_item.ship_date else 'N/A'
            expected_arrival_display = first_item.expected_arrival_date.strftime('%Y-%m-%d') if first_item.expected_arrival_date else 'N/A'
            
            # Get the checker name
            checker = first_item.checked_by.name if first_item.checked_by else 'Unknown'
            
            # Create submission summary
            submission = {
                'id': first_item.id,  # Use the first item's ID as reference
                'container_id': first_item.bol,
                'ship_date': ship_date_display,
                'expected_arrival': expected_arrival_display,
                'product_count': items.count(),
                'checked_by': checker,
                # Add other fields as needed
            }
            
            previous_submissions.append(submission)
    except Exception as e:
        print(f"Error fetching previous submissions: {e}")
        # Don't halt the page if there's an error with previous submissions
    page = request.GET.get('page', 1)
    paginator = Paginator(previous_submissions, 10)  # 10 submissions per page
    try:
        previous_submissions_page = paginator.page(page)
    except PageNotAnInteger:
        previous_submissions_page = paginator.page(1)
    except EmptyPage:
        previous_submissions_page = paginator.page(paginator.num_pages)
    # Render the page with user name and previous submissions
    return render(request, "inventory_shipment.html", {
        "user_name": user_name,
        "previous_submissions": previous_submissions_page,
        "is_paginated": previous_submissions_page.has_other_pages(),
        "page_obj": previous_submissions_page,  # For pagination in the template
    })

    return render(request, "inventory_shipment.html", {
        "user_name": user_name,
        "previous_submissions": previous_submissions
    })

@session_login_required
def get_product_item_num(request):
    clientId = request.GET.get("room_number")
    if not clientId:
        return JsonResponse({"success": False, "message": "Client ID is required"})
        
    try:
        # Use Lower database function to ensure case-insensitive matching
        from django.db.models.functions import Lower
        
        # Try first with Lower function for exact lowercase matching
        client_data = ProductData.objects.annotate(
            client_id_lower=Lower('client_id')
        ).filter(
            client_id_lower=clientId.lower()
        ).first()
        
        # If not found, fall back to iexact
        if not client_data:
            client_data = ProductData.objects.filter(client_id__iexact=clientId).first()
            
        if not client_data:
            return JsonResponse({"success": False, "message": "Product not found"})
            
        # Extract data from the found product
        get_item = client_data.item if client_data.item else ""
        supplier = client_data.supplier if client_data.supplier else "N.A."
        # Use description consistently (or fall back to item)
        product_name = client_data.description or client_data.item or ""
        
        print(f"get_product_item_num - client_id: {clientId}, product_name: {product_name}")
        return JsonResponse({
            "success": True, 
            "room_type": get_item, 
            "supplier": supplier, 
            "product_name": product_name,
            "client_id": client_data.client_id  # Return the actual client_id with correct case
        })
    except Exception as e:
        print(f"Error in get_product_item_num: {str(e)}")
        return JsonResponse({"success": False, "message": str(e)})


@session_login_required
def inventory_received(request):
    user_id = request.session.get("user_id")
    user_name = ""
    user = None

    if user_id:
        try:
            user = InvitedUser.objects.get(id=user_id)
            user_name = user.name
        except InvitedUser.DoesNotExist:
            pass

    if request.method == "POST":
        try:
            # Get form data
            container_id = request.POST.get("container_id")
            container_id_field = request.POST.get("container_id_field")  # Get the hidden field value
            
            # Use the hidden field value if it exists (from search), otherwise fallback to container_id
            final_container_id = container_id_field if container_id_field else container_id

            if container_id:
                container_id = container_id.strip().lower()
            if container_id_field:
                container_id_field = container_id_field.strip().lower()
            
            received_date = request.POST.get("received_date")
            is_editing = request.POST.get("is_editing") == "1"
            editing_record_id = request.POST.get("editing_record_id")
            product_count = int(request.POST.get("product_count", "0"))
            
            # Check if this container ID already exists in InventoryReceived for this user
            if not is_editing and final_container_id:
                existing_records = InventoryReceived.objects.filter(
                    container_id__iexact=final_container_id,
                    checked_by=user
                )
                if existing_records.exists():
                    is_editing = True
                    existing_records.delete()
            
            if is_editing and editing_record_id:
                try:
                    print(f"Editing record ID: {editing_record_id}")
                    # Get the original record to get the container ID
                    original_record = InventoryReceived.objects.get(id=editing_record_id)
                    container_id = original_record.container_id
                    print(f"Container ID: {container_id}")
                    
                    # Get all records for this container with their IDs
                    container_records = InventoryReceived.objects.filter(
                        container_id=container_id,
                        checked_by=user
                    )
                    print(f"Found {container_records.count()} records for container {container_id}")
                    
                    # Process each item in the form
                    success_count = 0
                    for i in range(product_count):
                        client_item = request.POST.get(f"client_item_{i}")
                        received_qty = int(request.POST.get(f"received_qty_{i}") or "0")
                        damaged_qty = int(request.POST.get(f"damaged_qty_{i}") or "0")
                        record_id = request.POST.get(f"record_id_{i}")  # Get the unique record ID
                        
                        print(f"Processing item {i}: client_item={client_item}, received_qty={received_qty}, damaged_qty={damaged_qty}, record_id={record_id}")
                        
                        if received_qty == 0:
                            continue
                        
                        # Find the specific record using its unique ID
                        record = InventoryReceived.objects.filter(
                            id=record_id,
                            container_id=container_id,
                            client_id=client_item
                        ).first()
                        
                        if record:
                            print(f"Found record to update: ID={record.id}, client_id={record.client_id}")
                            # Store old values for inventory adjustment
                            old_received = record.received_qty
                            old_damaged = record.damaged_qty
                            
                            # Update the record
                            record.received_date = received_date
                            record.received_qty = received_qty
                            record.damaged_qty = damaged_qty
                            record.save()
                            print(f"Updated record: received_qty={received_qty}, damaged_qty={damaged_qty}")
                
                            # Update inventory available quantity using our helper function
                            update_inventory_when_receiving(
                                client_id=client_item, 
                                received_qty=received_qty, 
                                damaged_qty=damaged_qty,
                                is_new=False,
                                old_received=old_received,   # <-- use the old value
                                old_damaged=old_damaged      # <-- use the old value
                            )
                            
                            # Add this line:
                            recalculate_quantity_available(client_item)
                            success_count += 1
                        else:
                            print(f"No record found for ID={record_id}, client_item={client_item} in container {container_id}")
                    
                    print(f"Successfully updated {success_count} items")
                    
                    # Update inventory calculated quantities
                    update_inventory_damaged_quantities()
                    update_inventory_received_quantities()
                    
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({
                            'success': True,
                            'message': f'Successfully updated {success_count} inventory items!'
                        })
                    messages.success(request, f"Successfully updated {success_count} inventory items!")
                    
                except InventoryReceived.DoesNotExist:
                    print(f"Record not found with ID {editing_record_id}")
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({
                            'success': False,
                            'message': 'Record not found'
                        })
                    messages.error(request, "Record not found")
                except Exception as e:
                    print(f"Error updating record: {str(e)}")
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({
                            'success': False,
                            'message': f'Error updating record: {str(e)}'
                        })
                    messages.error(request, f"Error updating record: {str(e)}")
            else:
                # Create new records for each product in the container
                success_count = 0
                for i in range(product_count):
                    client_item = request.POST.get(f"client_item_{i}")
                    received_qty = int(request.POST.get(f"received_qty_{i}") or "0")
                    damaged_qty = int(request.POST.get(f"damaged_qty_{i}") or "0")
                    
                    if received_qty == 0:
                        continue
                        
                    InventoryReceived.objects.create(
                        client_id=client_item,
                        item=client_item,
                        received_date=received_date,
                        received_qty=received_qty,
                        damaged_qty=damaged_qty,
                        checked_by=user,
                        container_id=final_container_id
                    )

                    # Update inventory available quantity using our helper function
                    update_inventory_when_receiving(
                        client_id=client_item, 
                        received_qty=received_qty, 
                        damaged_qty=damaged_qty,
                        is_new=True
                    )
                    # Add this line:
                    recalculate_quantity_available(client_item)
                        
                    success_count += 1

                # Update inventory calculated quantities
                update_inventory_damaged_quantities()
                update_inventory_received_quantities()
                
                if success_count > 0:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({
                            'success': True,
                            'message': f'Successfully received {success_count} inventory items!'
                        })
                    messages.success(request, f"Successfully received {success_count} inventory items!")
                else:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({
                            'success': False,
                            'message': 'No inventory items were received. Please enter quantities.'
                        })
                    messages.warning(request, "No inventory items were received. Please enter quantities.")
            
            if not request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return redirect("inventory_received")

        except Exception as e:
            print("error ::", e)
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'message': f'Error saving received inventory: {str(e)}'
                })
            messages.error(request, f"Error saving received inventory: {str(e)}")
            return redirect("inventory_received")

    # Get previous submissions
    previous_submissions = []
    already_processed_ids = set()
    
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    ir.container_id as display_container_id,
                    MAX(ir.received_date) as newest_date
                FROM inventory_received ir
                WHERE ir.checked_by_id = %s
                AND ir.container_id IS NOT NULL AND ir.container_id != ''
                GROUP BY ir.container_id
                ORDER BY MAX(ir.received_date) DESC
            """, [user.id])
            
            containers = cursor.fetchall()
            
        for container_row in containers:
            container_id = container_row[0]
            latest_date = container_row[1]
            
            if not container_id:
                continue
                
            items = InventoryReceived.objects.filter(
                checked_by=user
            ).filter(
                Q(container_id=container_id) | 
                (Q(client_id=container_id) & (Q(container_id__isnull=True) | Q(container_id='')))
            ).order_by('-received_date')
            
            if not items.exists():
                continue
                
            first_item = items[0]
            product_count = len(items)
            damaged_count = sum(1 for item in items if item.damaged_qty > 0)
            received_date = first_item.received_date.strftime('%Y-%m-%d') if first_item.received_date else 'N/A'
            
            previous_submissions.append({
                'id': first_item.id,
                'container_id': container_id,
                'client_id': first_item.client_id,
                'received_date': received_date,
                'product_count': product_count,
                'damaged_count': damaged_count,
                'checked_by': first_item.checked_by.name if first_item.checked_by else 'Unknown'
            })
    except Exception as e:
        print(f"Error fetching previous submissions: {e}")
        import traceback
        traceback.print_exc()
    
    # Pagination for previous submissions
    page = request.GET.get('page', 1)
    paginator = Paginator(previous_submissions, 10)
    try:
        previous_submissions_page = paginator.page(page)
    except PageNotAnInteger:
        previous_submissions_page = paginator.page(1)
    except EmptyPage:
        previous_submissions_page = paginator.page(paginator.num_pages)

    return render(request, "inventory_received.html", {
        "user_name": user_name,
        "previous_submissions": previous_submissions_page,
        "is_paginated": previous_submissions_page.has_other_pages(),
        "page_obj": previous_submissions_page,
    })

@session_login_required
def get_received_item_details(request):
    """API endpoint to get details of a specific received item"""
    record_id = request.GET.get('record_id')
    
    if not record_id:
        return JsonResponse({'success': False, 'message': 'Record ID is required'})
    
    try:
        # Debug info
        print(f"Getting details for received item record_id: {record_id}")
        
        record = InventoryReceived.objects.get(id=record_id)
        
        # Format dates for form fields
        received_date = record.received_date.strftime('%Y-%m-%d') if record.received_date else ''
        
        # Determine container ID - use stored container_id if available, otherwise client_id
        container_id = getattr(record, 'container_id', None) or record.client_id
        
        # Get product name from ProductData if available
        try:
            product = ProductData.objects.get(client_id__iexact=record.client_id)
            product_name = product.description or product.item
        except ProductData.DoesNotExist:
            product_name = record.item or "Unknown Product"
        
        # Debug info
        print(f"Record found: client_id={record.client_id}, received_qty={record.received_qty}, damaged_qty={record.damaged_qty}")
        print(f"Using container_id: {container_id}, received_date: {received_date}")
        
        return JsonResponse({
            'success': True,
            'record': {
                'id': record.id,
                'client_id': record.client_id,
                'container_id': container_id,
                'product_name': product_name,
                'received_date': received_date,
                'received_qty': record.received_qty,
                'damaged_qty': record.damaged_qty,
                'checked_by': record.checked_by.name if record.checked_by else 'Unknown'
            }
        })
        
    except InventoryReceived.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Record not found'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})

@session_login_required
def inventory_pull(request):
    
    user_id = request.session.get("user_id")
    user_name = ""



    if user_id:
        try:
            user = InvitedUser.objects.get(id=user_id)
            user_name = user.name
        except InvitedUser.DoesNotExist:
            pass
    
    previous_submissions = []
    
    try:
        previous_submissions = (
            WarehouseRequest.objects
            .select_related('requested_by', 'received_by')
            .order_by('-id')[:20]  # Show last 20 submissions, adjust as needed
        )
    except Exception as e:
        previous_submissions = []


    if request.method == "POST":
        try:
            # Get common data
            floor_number = request.POST.get('floor_number')
            active_tab = request.POST.get('active_tab', 'requested')
            common_date = request.POST.get('common_date')
            
            # Get all product IDs from the form
            product_ids = request.POST.getlist('selected_product_ids')
            client_item_ids = request.POST.getlist('client_item_ids')
            requested_quantities = request.POST.getlist('requested_quantities')
            received_quantities = request.POST.getlist('received_quantities')
            
            # Validate we have all necessary data
            if not product_ids or not floor_number:
                messages.error(request, "Missing required data")
                return redirect('inventory_pull')
            
            # Process based on active tab
            if active_tab == 'requested':
                # Create new warehouse requests
                for i, product_id in enumerate(product_ids):
                    client_item = client_item_ids[i]
                    requested_qty = int(requested_quantities[i])
                    
                    # Create warehouse request record
                    WarehouseRequest.objects.create(
                        floor_number=floor_number,
                        client_item=client_item,
                        requested_by=user,
                        quantity_requested=requested_qty,
                        quantity_received=0,  # Default 0 until received
                        quantity_sent=0,      # Default 0 for sent quantity
                        sent=False  # Default to No
                    )
                    
                    # Get the product data for logging
                prm = ProductData.objects.get(id=product_id)
                    
                messages.success(request, f"Warehouse request for {len(product_ids)} items submitted successfully!")
                
            elif active_tab == 'received':
                # Check if we have specific request IDs
                request_ids = request.POST.getlist('selected_request_ids')
                
                # Debug logs
                print(f"Processing received tab with {len(request_ids)} request IDs")
                print(f"Request IDs: {request_ids}")
                print(f"Client items: {client_item_ids}")
                print(f"Requested quantities: {requested_quantities}")
                print(f"Received quantities: {received_quantities}")
                
                if request_ids:
                    # Update specific warehouse requests by ID
                    success_count = 0
                    for i, request_id in enumerate(request_ids):
                        client_item = client_item_ids[i]
                        requested_qty = int(requested_quantities[i])
                        received_qty = int(received_quantities[i])
                        
                        print(f"Processing request ID {request_id}: client_item={client_item}, requested={requested_qty}, received={received_qty}")
                        
                        try:
                            # Get the specific warehouse request by ID
                            warehouse_request = WarehouseRequest.objects.get(id=request_id)
                            print(f"Found warehouse request: {warehouse_request}")
                            
                            # Calculate how much additional quantity is being received now
                            previous_received = warehouse_request.quantity_received
                            additional_received = received_qty - previous_received
                            
                            print(f"Previous received: {previous_received}, New received: {received_qty}, Additional: {additional_received}")
                            
                            # Update the received quantity
                            warehouse_request.quantity_received = received_qty
                            warehouse_request.received_by = user
                            
                            # Update sent status based on whether requested equals received
                            warehouse_request.sent = (warehouse_request.quantity_received == warehouse_request.quantity_requested)
                            warehouse_request.save()
                            success_count += 1
                            print(f"Updated warehouse request {request_id}: quantity_received={received_qty}, sent={warehouse_request.sent}")
                            
                            # Only update inventory if additional quantity is being received
                            if additional_received > 0:
                                print(f"Deducting {additional_received} from warehouse inventory")
                                # Update inventory for received items
                                try:
                                    # Use case-insensitive lookup for client_id
                                    inventory = Inventory.objects.filter(client_id__iexact=client_item).first()
                                    if inventory:
                                        # We don't need to deduct from hotel_warehouse_quantity again
                                        # as it was already deducted when the item was sent
                                        print(f"Recording reception for {client_item} - not deducting from warehouse again")
                                    
                                        # Calculate available quantity (ensure it's not negative)
                                        available_qty = max(0, inventory.quantity_available - received_qty)
                                        
                                        # Create pull inventory record for tracking
                                        PullInventory.objects.create(
                                            client_id=client_item,
                                            item=inventory.item,
                                            qty_pulled=received_qty,
                                            pulled_by=user,
                                            pulled_date=common_date,
                                            floor=floor_number,
                                            available_qty=available_qty,
                                            qty_available_after_pull=inventory.quantity_available
                                        )
                                except Exception as inv_error:
                                    messages.warning(request, f"Error updating inventory for {client_item}: {str(inv_error)}")
                        except WarehouseRequest.DoesNotExist:
                            messages.warning(request, f"Warehouse request with ID {request_id} not found")
                    
                    if success_count > 0:
                        messages.success(request, f"Updated {success_count} warehouse requests successfully!")
                    else:
                        messages.warning(request, "No warehouse requests were updated")
                else:
                    # Legacy fallback - update based on client_item and floor
                    for i, product_id in enumerate(product_ids):
                        client_item = client_item_ids[i]
                        requested_qty = int(requested_quantities[i])
                        received_qty = int(received_quantities[i])
                        
                        # Find existing warehouse request
                        warehouse_request = WarehouseRequest.objects.filter(
                            floor_number=floor_number,
                            client_item=client_item,
                            sent=False
                        ).first()
                        
                        if warehouse_request:
                            # Calculate how much additional quantity is being received now
                            previous_received = warehouse_request.quantity_received
                            additional_received = received_qty - previous_received
                            
                            print(f"Legacy handler - Previous received: {previous_received}, New received: {received_qty}, Additional: {additional_received}")
                            
                            # Update the received quantity
                            warehouse_request.quantity_received = received_qty
                            
                            # Update sent status based on whether requested equals received
                            warehouse_request.sent = (warehouse_request.quantity_received == warehouse_request.quantity_requested)
                            warehouse_request.save()
                            
                            # Only update inventory if additional quantity is being received
                            if additional_received > 0:
                                print(f"Legacy handler - Deducting {additional_received} from warehouse inventory")
                                # Update inventory for received items
                                try:
                                    # Use case-insensitive lookup for client_id
                                    inventory = Inventory.objects.filter(client_id__iexact=client_item).first()
                                    if inventory:
                                        # We don't need to deduct from hotel_warehouse_quantity again
                                        # as it was already deducted when the item was sent
                                        print(f"Recording reception for {client_item} - not deducting from warehouse again")
                                    
                                        # Calculate available quantity (ensure it's not negative)
                                        available_qty = max(0, inventory.quantity_available - received_qty)
                                        
                                        # Create pull inventory record for tracking
                                        PullInventory.objects.create(
                                            client_id=client_item,
                                            item=inventory.item,
                                            qty_pulled=received_qty,
                                            pulled_by=user,
                                            pulled_date=common_date,
                                            floor=floor_number,
                                            available_qty=available_qty,
                                            qty_available_after_pull=inventory.quantity_available
                                        )
                                except Exception as inv_error:
                                    messages.warning(request, f"Error updating inventory for {client_item}: {str(inv_error)}")
                        else:
                            messages.warning(request, f"No warehouse request found for item {client_item} on floor {floor_number}")
                    
                    messages.success(request, f"Received {len(product_ids)} items successfully!")
            
            return redirect('inventory_pull')
            
        except Exception as e:
            messages.error(request, f"Error processing inventory pull: {str(e)}")
            return redirect('inventory_pull')
            
    return render(request, "inventory_pull.html", {
        "user_name": user_name,
        "previous_submissions": previous_submissions,
    })

@session_login_required
def hotel_warehouse(request):
    # Get all previous warehouse submissions
    previous_submissions = (
        WarehouseRequest.objects
        .select_related('requested_by', 'received_by')
        .order_by('-id')[:30]  # Show last 30, adjust as needed
    )
    
    # Process previous submissions to determine if all items for each floor are sent
    from itertools import groupby
    from django.db.models import Count, Sum
    
    # Group submissions by floor number and check if all items are sent
    floor_data = []
    floor_groups = {}
    
    # First group by floor number
    for submission in previous_submissions:
        floor_num = submission.floor_number
        if floor_num not in floor_groups:
            floor_groups[floor_num] = {
                'floor_number': floor_num,
                'items': [],
                'all_sent': True,  # Start with True and set to False if any item is not sent
                'total_items': 0,
                'total_requested': 0,
                'total_sent': 0,
                'total_received': 0,
                'requested_by': None,
                'sent_by': None,
                'sent_date': None
            }
        
        # Add item to the floor group
        floor_groups[floor_num]['items'].append(submission)
        
        # Update totals
        floor_groups[floor_num]['total_items'] += 1
        floor_groups[floor_num]['total_requested'] += submission.quantity_requested
        floor_groups[floor_num]['total_sent'] += submission.quantity_sent
        floor_groups[floor_num]['total_received'] += submission.quantity_received
        
        # Set requested_by if not set yet
        if floor_groups[floor_num]['requested_by'] is None and submission.requested_by:
            floor_groups[floor_num]['requested_by'] = submission.requested_by.name
            
        # Debug sent_by_id and sent_date values
        print(f"Floor {floor_num}, Item {submission.id}: sent_by_id={submission.sent_by_id}, sent_date={submission.sent_date}, sent={submission.sent}")
        
        # Set sent_by if not set yet and this item was sent
        if floor_groups[floor_num]['sent_by'] is None and submission.sent_by_id and submission.sent:
            try:
                sent_by_user = InvitedUser.objects.get(id=submission.sent_by_id)
                floor_groups[floor_num]['sent_by'] = sent_by_user.name
                print(f"Setting floor {floor_num} sent_by to {sent_by_user.name}")
            except:
                floor_groups[floor_num]['sent_by'] = f"User ID {submission.sent_by_id}"
                print(f"Could not find user with ID {submission.sent_by_id}")
            
        # Set sent_date if not set yet and this item was sent
        if floor_groups[floor_num]['sent_date'] is None and submission.sent_date and submission.sent:
            floor_groups[floor_num]['sent_date'] = submission.sent_date
            print(f"Setting floor {floor_num} sent_date to {submission.sent_date}")
        
        # Update all_sent status
        if not submission.sent:
            floor_groups[floor_num]['all_sent'] = False
    
    # Convert dictionary to list for template
    floor_data = list(floor_groups.values())
    
    if request.method == 'POST':
        action = request.POST.get('action')
        floor_number = request.POST.get('floor_number')
        
        # Get the current user
        user_id = request.session.get('user_id')
        user = None
        if user_id:
            try:
                user = InvitedUser.objects.get(id=user_id)
                print(f"Current user: {user.name} (ID: {user.id})")
            except InvitedUser.DoesNotExist:
                print(f"User with ID {user_id} not found")
                messages.error(request, "User session not found. Please log in again.")
                return redirect('user_login')
        else:
            print("No user_id in session")
            messages.error(request, "User session not found. Please log in again.")
            return redirect('user_login')
        
        if action == 'update_sent_qty':
            try:
                # Check if we need to process multiple items
                if 'items' in request.POST:
                    # Handle modal submission with individual item quantities
                    items_data = json.loads(request.POST.get('items'))
                    
                    for item_data in items_data:
                        client_item = item_data['client_item']
                        item_id = item_data.get('id')  # Get the specific item ID
                        quantity_sent = int(item_data['quantity_sent'])
                        
                        # Update the warehouse request by ID if provided
                        if item_id:
                            request_item = WarehouseRequest.objects.filter(id=item_id).first()
                        else:
                            # Fallback to the old method if ID is not provided
                            request_item = WarehouseRequest.objects.filter(
                                floor_number=floor_number,
                                client_item=client_item
                            ).first()
                        
                        if request_item:
                            # Calculate how much additional quantity is being sent now
                            previous_sent = request_item.quantity_sent
                            additional_sent = quantity_sent - previous_sent
                            
                            print(f"Previous sent: {previous_sent}, New sent: {quantity_sent}, Additional: {additional_sent}")
                            
                            # Update the sent quantity
                            request_item.quantity_sent = quantity_sent
                            # Update sent status based on quantity_received
                            request_item.sent = (request_item.quantity_received == request_item.quantity_requested)
                            
                            # Always set sent_by and sent_date if quantity is sent
                            if quantity_sent > 0:
                                from datetime import datetime
                                request_item.sent_by = user
                                request_item.sent_date = datetime.now()
                                print(f"Setting sent_by={user.name} and sent_date={datetime.now()} for item {request_item.id}")
                                
                            request_item.save()
                            
                            # Only update inventory if additional quantity is being sent
                            if additional_sent > 0:
                                print(f"Deducting {additional_sent} from warehouse inventory")
                                # Update inventory for sent items
                                try:
                                    # Use case-insensitive lookup for client_id
                                    inventory = Inventory.objects.filter(client_id__iexact=client_item).first()
                                    if inventory:
                                        # Update inventory with additional sent quantity - use hotel_warehouse_quantity
                                        current_warehouse_qty = inventory.hotel_warehouse_quantity or 0
                                        inventory.hotel_warehouse_quantity = max(0, current_warehouse_qty - additional_sent)
                                        inventory.save()
                                        print(f"Updated inventory for {client_item}: warehouse quantity from {current_warehouse_qty} to {inventory.hotel_warehouse_quantity}")
                                except Exception as inv_error:
                                    messages.warning(request, f"Error updating inventory for {client_item}: {str(inv_error)}")
                    
                    messages.success(request, f"Updated quantities for floor {floor_number} successfully!")
                else:
                    # Handle table row update - single floor total
                    sent_qty = int(request.POST.get('sent_qty', 0))
                    sent_status = request.POST.get('sent_status', '') == 'Yes'
                    
                    # Update all requests for this floor with quantity_sent,
                    # but determine sent status based on quantity_received in the database
                    requests = WarehouseRequest.objects.filter(floor_number=floor_number)
                    for req in requests:
                        # Calculate how much additional quantity is being sent now
                        previous_sent = req.quantity_sent
                        additional_sent = sent_qty - previous_sent
                        
                        req.quantity_sent = sent_qty
                        # Use quantity_received for status determination
                        req.sent = (req.quantity_received == req.quantity_requested)
                        
                        # Always set sent_by and sent_date if quantity is sent
                        if sent_qty > 0:
                            from datetime import datetime
                            req.sent_by = user
                            req.sent_date = datetime.now()
                            print(f"Setting sent_by={user.name} and sent_date={datetime.now()} for item {req.id}")
                            
                        req.save()
                        
                        # Only update inventory if additional quantity is being sent
                        if additional_sent > 0:
                            try:
                                # Use case-insensitive lookup for client_id
                                inventory = Inventory.objects.filter(client_id__iexact=req.client_item).first()
                                if inventory:
                                    # Update inventory with additional sent quantity - use hotel_warehouse_quantity
                                    current_warehouse_qty = inventory.hotel_warehouse_quantity or 0
                                    inventory.hotel_warehouse_quantity = max(0, current_warehouse_qty - additional_sent)
                                    inventory.save()
                                    print(f"Updated inventory for {req.client_item}: warehouse quantity from {current_warehouse_qty} to {inventory.hotel_warehouse_quantity}")
                            except Exception as inv_error:
                                messages.warning(request, f"Error updating inventory for {req.client_item}: {str(inv_error)}")
                    
                    requests_updated = len(requests)
                    
                    if requests_updated > 0:
                        messages.success(request, f"Updated {requests_updated} items for floor {floor_number}!")
                    else:
                        messages.warning(request, f"No items found for floor {floor_number}")
                        
            except Exception as e:
                messages.error(request, f"Error updating quantities: {str(e)}")
            
            return redirect('hotel_warehouse')
    
    # Get warehouse requests grouped by floor
    warehouse_data = []
    
    # Get unique floor numbers
    floor_numbers = WarehouseRequest.objects.filter(sent=False).values_list('floor_number', flat=True).distinct()
    
    for floor_number in floor_numbers:
        # Get only PENDING requests for this floor (sent=False)
        floor_requests = WarehouseRequest.objects.filter(floor_number=floor_number, sent=False)
        
        # Skip empty floors
        if not floor_requests.exists():
            continue
            
        # Get the first request to use for requested_by
        first_request = floor_requests.first()
        requested_by_name = first_request.requested_by.name if first_request.requested_by else 'Unknown'
        
        # Calculate totals - ONLY for pending requests
        item_count = floor_requests.count()
        quantity_requested = sum(req.quantity_requested for req in floor_requests)
        quantity_sent = sum(req.quantity_sent for req in floor_requests)
        
        # A floor is sent only if all items have quantity_received matching quantity_requested
        all_sent = all(req.quantity_received == req.quantity_requested for req in floor_requests)
        
        warehouse_data.append({
            'floor_number': floor_number,
            'requested_by': requested_by_name,
            'item_count': item_count,
            'quantity_requested': quantity_requested,
            'quantity_sent': quantity_sent,
            'sent': True if all_sent else False
        })
    
    return render(request, 'hotel_warehouse.html', {
        "previous_submissions": previous_submissions,
        "floor_data": floor_data,
        "warehouse_requests": warehouse_data
    })

@session_login_required
def inventory_received_item_num(request):
    clientId = request.GET.get("client_item")
    try:
        client_data_fetched = Inventory.objects.get(client_id__iexact=clientId)
        get_item = client_data_fetched.item if client_data_fetched.item else ""
        product_name = ProductData.objects.filter(item=client_data_fetched.item).values_list('description', flat=True).first() or ""
        return JsonResponse({"success": True, "product_item": get_item, "product_name": product_name})
    except RoomData.DoesNotExist:
        return JsonResponse({"success": False})


@login_required
def save_schedule(request):
    if request.method == "POST":
        post_data = request.POST
        schedule_id = post_data.get("schedule_id") or ""
        phase = post_data.get("phase") or ""
        floor = post_data.get("floor") or ""
        production_starts = post_data.get("production_starts") or ""
        production_ends = post_data.get("production_ends") or ""
        shipping_depature = post_data.get("shipping_depature") or ""
        shipping_arrival = post_data.get("shipping_arrival") or ""
        custom_clearing_starts = post_data.get("custom_clearing_starts") or ""
        custom_clearing_ends = post_data.get("custom_clearing_ends") or ""
        arrive_on_site = post_data.get("arrive_on_site") or ""
        pre_work_starts = post_data.get("pre_work_starts") or ""
        pre_work_ends = post_data.get("pre_work_ends") or ""
        install_starts = post_data.get("install_starts") or ""
        install_ends = post_data.get("install_ends") or ""
        post_work_starts = post_data.get("post_work_starts") or ""
        post_work_ends = post_data.get("post_work_ends") or ""
        floor_completed = post_data.get("floor_completed") or ""
        floor_closes = post_data.get("floor_closes") or ""
        floor_opens = post_data.get("floor_opens") or ""
        try:
            if schedule_id:
                print("Editing ", custom_clearing_ends)
                installation = Schedule.objects.get(id=schedule_id)
                installation.phase = phase
                installation.floor = floor
                installation.production_starts = parse_date(production_starts)
                installation.production_ends = parse_date(production_ends)
                installation.shipping_depature = parse_date(shipping_depature)
                installation.shipping_arrival = parse_date(shipping_arrival)
                installation.custom_clearing_starts = parse_date(custom_clearing_starts)
                installation.custom_clearing_ends = parse_date(custom_clearing_ends)
                installation.arrive_on_site = parse_date(arrive_on_site)
                installation.pre_work_starts = parse_date(pre_work_starts)
                installation.pre_work_ends = parse_date(pre_work_ends)
                installation.install_starts = parse_date(install_starts)
                installation.install_ends = parse_date(install_ends)
                installation.post_work_starts = parse_date(post_work_starts)
                installation.post_work_ends = parse_date(post_work_ends)
                installation.floor_completed = parse_date(floor_completed)
                installation.floor_closes = parse_date(floor_closes)
                installation.floor_opens = parse_date(floor_opens)
                installation.save()
            else:
                print("under new")
                print(post_data)
                Schedule.objects.create(
                    phase=phase,
                    floor=floor,
                    production_starts=parse_date(production_starts),
                    production_ends=parse_date(production_ends),
                    shipping_depature=parse_date(shipping_depature),
                    shipping_arrival=parse_date(shipping_arrival),
                    custom_clearing_starts=parse_date(custom_clearing_starts),
                    custom_clearing_ends=parse_date(custom_clearing_ends),
                    arrive_on_site=parse_date(arrive_on_site),
                    pre_work_starts=parse_date(pre_work_starts),
                    pre_work_ends=parse_date(pre_work_ends),
                    install_starts=parse_date(install_starts),
                    install_ends=parse_date(install_ends),
                    post_work_starts=parse_date(post_work_starts),
                    post_work_ends=parse_date(post_work_ends),
                    floor_completed=parse_date(floor_completed),
                    floor_closes=parse_date(floor_closes),
                    floor_opens=parse_date(floor_opens),
                )
            return JsonResponse({"success": True})

        except Installation.DoesNotExist:
            return JsonResponse({"error": "Installation not found."}, status=404)

    return JsonResponse({"error": "Invalid request"}, status=400)


@login_required
def save_product_data(request):
    if request.method == "POST":
        post_data = request.POST
        print("hhhhhhhh", post_data)

        product_id = post_data.get("product_id")
        image = request.FILES.get('image')
        item = post_data.get("item", "").strip()
        client_id = post_data.get("client_id", "").strip()
        description = post_data.get("description") or 0

        supplier = post_data.get("supplier")
        client_selected = post_data.get("client_selected") or 0
        image = request.FILES.get('image')
        try:
            if product_id:
                print("inside")
                installation = ProductData.objects.get(id=product_id)
                if request.POST.get('delete_image') == '1':
                    if installation.image:
                        installation.image.delete(save=False)
                        installation.image = None  
                installation.product_id = product_id
                installation.item = item
                installation.client_id = client_id
                installation.description = description
                installation.supplier = supplier
                installation.client_selected = client_selected
                if image:  # Only update if a new image is uploaded
                    installation.image = image
                installation.save()
            else:
                print("Adding new row")
                ProductData.objects.create(
                    item=item,
                    client_id=client_id,
                    description=description,
                    supplier=supplier,
                    client_selected=client_selected,
                    image=image,
                )

            return JsonResponse({"success": True})

        except Installation.DoesNotExist:
            return JsonResponse({"error": "Installation not found."}, status=404)

    return JsonResponse({"error": "Invalid request"}, status=400)


@login_required
def delete_installation(request):
    if request.method == "POST":
        model_id = request.POST.get("model_id")
        try:
            room_model = Installation.objects.get(id=model_id)
            room_model.delete()
            return JsonResponse({"success": "Room Model deleted."})
        except Installation.DoesNotExist:
            return JsonResponse({"error": "Room Model not found."})
    return JsonResponse({"error": "Invalid request."})


@session_login_required
def user_logout(request):
    request.session.flush()
    return redirect("user_login")


@session_login_required
def home(request):
    user_id = request.session.get("user_id")

    if not user_id:
        return redirect("user_login")

    try:
        user = InvitedUser.objects.get(id=user_id)

        # Ensure roles are processed and stored in session for use in base template
        user_roles = []
        if user.role:
             # Handle potential string representation of list
            roles_raw = user.role
            if isinstance(roles_raw, str):
                try:
                    # Attempt to parse string as list (e.g., "['admin', 'inventory']")
                    parsed_roles = ast.literal_eval(roles_raw)
                    if isinstance(parsed_roles, list):
                        roles_raw = parsed_roles
                    else:
                        # Handle simple comma-separated string (e.g., "admin, inventory")
                        roles_raw = [r.strip() for r in roles_raw.split(',')]
                except (ValueError, SyntaxError):
                     # Fallback for simple comma-separated string if literal_eval fails
                     roles_raw = [r.strip() for r in roles_raw.split(',')]
            
            # Ensure it's a list and process
            if isinstance(roles_raw, list):
                user_roles = [
                    role.strip().lower() for role in roles_raw if isinstance(role, str)
                ]
            
        # Store processed roles in session
        request.session['user_roles'] = user_roles 

        return render(
            request, "home.html", {"name": user.name, "roles": user_roles}
        )
    except InvitedUser.DoesNotExist:
        # Clear potentially invalid session data if user doesn't exist
        request.session.flush()
        return redirect("user_login")

@login_required
@admin_required
def chat_history(request):
    sessions = ChatSession.objects.prefetch_related('chat_history').order_by('-created_at')
    return render(request, 'chat_history.html', {'sessions': sessions})

@login_required
def view_chat_history(request, session_id):
    session = get_object_or_404(ChatSession, id=session_id)
    chat_messages = session.chat_history.order_by('created_at')[:100]  # from related_name
    return render(request, 'view_chat_history.html', {
        'session': session,
        'chat_messages': chat_messages
    })

@login_required
def product_room_model_list(request):
    """
    Display a list of all product room model mappings with related data.
    """
    # Get all mappings with related data for efficient template rendering
    product_room_model_list = ProductRoomModel.objects.select_related('product_id', 'room_model_id').all()
    
    # Get products and room models for the dropdown in the add/edit form
    products = ProductData.objects.all()
    room_models = RoomModel.objects.all().order_by(Lower('room_model'))
    
    return render(request, "product_room_model_list.html", {
        "product_room_model_list": product_room_model_list,
        "products": products,
        "room_models": room_models
    })

@login_required
def save_product_room_model(request):
    """
    Add or update a product room model mapping.
    """
    if request.method == "POST":
        mapping_id = request.POST.get("mapping_id")
        product_id = request.POST.get("product_id")
        room_model_id = request.POST.get("room_model_id")
        quantity = request.POST.get("quantity", "1").strip()
        
        # Validate inputs
        if not product_id or not room_model_id:
            return JsonResponse({"error": "Product and Room Model are required"}, status=400)
            
        try:
            quantity = int(quantity)
            if quantity < 0:
                return JsonResponse({"error": "Quantity must be a positive number"}, status=400)
        except ValueError:
            return JsonResponse({"error": "Quantity must be a valid number"}, status=400)

        # Check for existing mappings with the same product and room model (avoid duplicates)
        existing_mapping = ProductRoomModel.objects.filter(
            product_id_id=product_id, 
            room_model_id_id=room_model_id
        )
        
        if mapping_id:
            existing_mapping = existing_mapping.exclude(id=mapping_id)
        
        if existing_mapping.exists():
            return JsonResponse(
                {"error": "A mapping for this product and room model already exists"}, 
                status=400
            )
        
        try:
            product = ProductData.objects.get(id=product_id)
            room_model = RoomModel.objects.get(id=room_model_id)
            
            if mapping_id:
                # Update existing mapping
                mapping = ProductRoomModel.objects.get(id=mapping_id)
                mapping.quantity = quantity
                mapping.save()
            else:
                # Create new mapping
                ProductRoomModel.objects.create(
                    product_id=product,
                    room_model_id=room_model,
                    quantity=quantity
                )
                
            return JsonResponse({"success": True})
            
        except (ProductData.DoesNotExist, RoomModel.DoesNotExist):
            return JsonResponse({"error": "Product or Room Model not found"}, status=404)
        except ProductRoomModel.DoesNotExist:
            return JsonResponse({"error": "Mapping not found"}, status=404)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    
    return JsonResponse({"error": "Invalid request method"}, status=405)

@login_required
def delete_product_room_model(request):
    """
    Delete a product room model mapping.
    """
    if request.method == "POST":
        model_id = request.POST.get("model_id")
        
        try:
            mapping = ProductRoomModel.objects.get(id=model_id)
            mapping.delete()
            return JsonResponse({"success": True})
        except ProductRoomModel.DoesNotExist:
            return JsonResponse({"error": "Mapping not found"}, status=404)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    
    return JsonResponse({"error": "Invalid request method"}, status=405)

@session_login_required
def get_floor_products(request):
    floor_number = request.GET.get("floor_number")
    user_id = request.session.get("user_id")
    
    try:
        # Get the current user
        user = get_object_or_404(InvitedUser, id=user_id) if user_id else None
        
        # Use raw SQL query to get products with total quantity needed
        products = ProductData.objects.raw("""
            WITH room_counts AS (
                SELECT rm.id AS room_model_id, COUNT(*) AS room_count
                FROM room_data rd
                JOIN room_model rm ON rd.room_model_id = rm.id
                WHERE rd.floor = %s
                GROUP BY rm.id
            ),
            pulled_quantities AS (
                SELECT client_id, item, SUM(qty_pulled) as total_pulled
                FROM pull_inventory
                GROUP BY client_id, item
            ),
            warehouse_requests AS (
                SELECT client_item, SUM(quantity_requested) as total_requested, 
                       SUM(quantity_received) as total_received
                FROM warehouse_request
                WHERE floor_number = %s
                GROUP BY client_item
            )
            SELECT pd.id, pd.item, pd.client_id, pd.description, pd.supplier,
                   SUM(prm.quantity * rc.room_count) AS total_quantity_needed,
                   COALESCE(inv.quantity_installed, 0) AS quantity_installed,
                   COALESCE(inv.quantity_available, 0) AS available_qty,
                   COALESCE(inv.hotel_warehouse_quantity, 0) AS hotel_warehouse_quantity,
                   COALESCE(pq.total_pulled, 0) AS pulled_quantity,
                   COALESCE(wr.total_requested, 0) AS requested_quantity,
                   COALESCE(wr.total_received, 0) AS received_quantity
            FROM product_room_model prm
            JOIN product_data pd ON prm.product_id = pd.id
            JOIN room_counts rc ON prm.room_model_id = rc.room_model_id
            LEFT JOIN inventory inv ON pd.client_id = inv.client_id
            LEFT JOIN pulled_quantities pq ON pd.client_id = pq.client_id AND pd.item = pq.item
            LEFT JOIN warehouse_requests wr ON pd.client_id = wr.client_item
            GROUP BY pd.id, pd.client_id, pd.description, pd.supplier, inv.quantity_installed, 
                     inv.quantity_available, inv.hotel_warehouse_quantity, pq.total_pulled,
                     wr.total_requested, wr.total_received
            ORDER BY pd.client_id
        """, [floor_number, floor_number])
        
        # Also get specific warehouse request items for this floor
        # Also get specific warehouse request items for this floor - try both string and int matching
        # First try with string comparison
        warehouse_requests_str = WarehouseRequest.objects.filter(
            floor_number=str(floor_number),
            sent=False
        ).select_related('requested_by')
        
        # Then try with integer comparison
        try:
            floor_number_int = int(floor_number)
            warehouse_requests_int = WarehouseRequest.objects.filter(
                floor_number=floor_number_int,
                sent=False
            ).select_related('requested_by')
            
            # Combine both querysets
            warehouse_requests = warehouse_requests_str | warehouse_requests_int
            warehouse_requests = warehouse_requests.distinct()
        except ValueError:
            # If floor_number can't be converted to int, just use the string results
            warehouse_requests = warehouse_requests_str
        
        # Debug info
        print(f"Found {warehouse_requests.count()} warehouse requests for floor {floor_number}")
        
        # If no requests were found, log more information for debugging
        if warehouse_requests.count() == 0:
                
            # Print all unsent warehouse requests for debugging
            all_unsent = WarehouseRequest.objects.filter(sent=False)
            print(f"Total unsent warehouse requests in system: {all_unsent.count()}")
            for req in all_unsent:
                print(f"Unsent request: floor={req.floor_number}, client_item={req.client_item}, requested={req.quantity_requested}")
                
        # Print details of each warehouse request
        for i, req in enumerate(warehouse_requests):
            print(f"  Request #{i+1}: ID={req.id}, floor={req.floor_number}, client_item={req.client_item}, quantity_requested={req.quantity_requested}, quantity_received={req.quantity_received}")
        
        # Create lists to store all warehouse requests and a mapping dictionary 
        all_warehouse_requests = []
        warehouse_requests_dict = {}
        
        for req in warehouse_requests:
            # Try to find the matching product for this client item
            product = ProductData.objects.filter(client_id__iexact=req.client_item).first()
            
            request_data = {
                'id': req.id,
                'client_item': req.client_item,
                'product_id': product.id if product else None,
                'description': product.description if product else f"Unknown Product ({req.client_item})",
                'quantity_requested': req.quantity_requested,
                'quantity_received': req.quantity_received,
                'requested_by': req.requested_by.name if req.requested_by else 'Unknown',
                'sent': req.sent
            }
            
            # Add to the all_warehouse_requests list
            all_warehouse_requests.append(request_data)
            
            # If we found a product, add to the product-specific dictionary too
            if product:
                key = f"{product.id}_{req.client_item}"
                if key not in warehouse_requests_dict:
                    warehouse_requests_dict[key] = []
                
                warehouse_requests_dict[key].append(request_data)
        
        result_products = []
        for product in products:
            # Calculate remaining quantity needed after subtracting already pulled quantity
            remaining_quantity = max(0, product.total_quantity_needed - product.pulled_quantity)
            
            # Get warehouse requests for this product using the product_id+client_id key
            key = f"{product.id}_{product.client_id}"
            product_requests = warehouse_requests_dict.get(key, [])
            
            result_products.append({
                "id": product.id,
                "client_id": product.client_id,
                "description": product.description,
                "quantity": remaining_quantity,  # Use remaining quantity instead of total
                "available_qty": product.available_qty,
                "hotel_warehouse_quantity": product.hotel_warehouse_quantity,
                "supplier": product.supplier,
                "quantity_installed": product.quantity_installed,
                "total_quantity_needed": product.total_quantity_needed,
                "pulled_quantity": product.pulled_quantity,
                "requested_quantity": getattr(product, 'requested_quantity', 0),
                "received_quantity": getattr(product, 'received_quantity', 0),
                "warehouse_requests": product_requests  # Add specific request items
            })
            
        # Add standalone warehouse requests for items without matching products
        for request in all_warehouse_requests:
            if request['product_id'] is None:
                # Create a dummy product entry for this request
                result_products.append({
                    "id": f"dummy_{request['id']}",  # Create a unique ID
                    "client_id": request['client_item'],
                    "description": request['description'],
                    "quantity": 0,
                    "available_qty": 0,
                    "hotel_warehouse_quantity": 0,
                    "supplier": "",
                    "quantity_installed": 0,
                    "total_quantity_needed": 0,
                    "pulled_quantity": 0,
                    "requested_quantity": request['quantity_requested'],
                    "received_quantity": request['quantity_received'],
                    "warehouse_requests": [request]  # Include this single request
            })
            
        return JsonResponse({
            "success": True,
            "products": result_products,
            "all_warehouse_requests": all_warehouse_requests
        })
    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": str(e)
        })

# Helper function to generate XLS response
def _generate_xls_response(data, filename, sheet_name="Sheet1"):
    response = HttpResponse(content_type='application/ms-excel')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet(sheet_name)

    # Sheet header, first row
    row_num = 0
    font_style = xlwt.XFStyle()
    font_style.font.bold = True

    # Infer columns from the first dictionary in the list
    if data:
        columns = list(data[0].keys())
        for col_num, column_title in enumerate(columns):
            ws.write(row_num, col_num, column_title, font_style)

        # Sheet body, remaining rows
        font_style = xlwt.XFStyle()

        for row_data in data:
            row_num += 1
            for col_num, col_key in enumerate(columns):
                value = row_data.get(col_key, '')
                # Handle potential date/datetime objects if necessary
                if isinstance(value, (date, datetime)):
                    value = value.strftime('%Y-%m-%d %H:%M:%S') # Or just %Y-%m-%d
                ws.write(row_num, col_num, value, font_style)
    else:
         # Write a message if no data
         ws.write(0, 0, "No data found for the selected criteria.", font_style)


    wb.save(response)
    return response

# Helper function to get floor products data (extracted SQL)
def _get_floor_products_data(floor_number):
    try:
        with connection.cursor() as cursor:

            sql_query = """
             WITH room_counts AS (
                SELECT rm.id AS room_model_id, COUNT(*) AS room_count
                FROM room_data rd
                JOIN room_model rm ON rd.room_model_id = rm.id
                WHERE rd.floor = %s
                GROUP BY rm.id
            ),
            pulled_quantities AS (
                SELECT client_id, item, SUM(qty_pulled) as total_pulled
                FROM pull_inventory
                GROUP BY client_id, item
            )
            SELECT pd.id, pd.item, pd.client_id, pd.description, pd.supplier,
                   SUM(prm.quantity * rc.room_count) AS total_quantity_needed,
                   COALESCE(inv.quantity_installed, 0) AS quantity_installed,
                   COALESCE(inv.quantity_available, 0) AS available_qty,
                   COALESCE(inv.hotel_warehouse_quantity, 0) AS hotel_warehouse_quantity,
                   COALESCE(pq.total_pulled, 0) AS pulled_quantity
            FROM product_room_model prm
            JOIN product_data pd ON prm.product_id = pd.id
            JOIN room_counts rc ON prm.room_model_id = rc.room_model_id
            LEFT JOIN inventory inv ON pd.client_id = inv.client_id
            LEFT JOIN pulled_quantities pq ON pd.client_id = pq.client_id AND pd.item = pq.item
            GROUP BY pd.id, pd.client_id, pd.description, pd.supplier, inv.quantity_installed, inv.quantity_available, inv.hotel_warehouse_quantity, pq.total_pulled
            ORDER BY pd.client_id"""
            
            print("sql_query",sql_query)
            cursor.execute(sql_query, [floor_number])

            columns = [col[0] for col in cursor.description]
            products = [dict(zip(columns, row)) for row in cursor.fetchall()]

        result_products = []
        for product in products:
            total_needed = product.get('total_quantity_needed', 0) or 0
            pulled = product.get('pulled_quantity', 0) or 0
            remaining_quantity = max(0, total_needed - pulled)

            result_products.append({
                "id": product.get('id'),
                "item": product.get('item'),
                "client_id": product.get('client_id'),
                "description": product.get('description'),
                "supplier": product.get('supplier'),
                "total_quantity_needed": total_needed,
                "pulled_quantity": pulled,
                "remaining_quantity_needed": remaining_quantity, # Renamed from 'quantity' for clarity
                "available_qty": product.get('available_qty', 0) or 0,
                "hotel_warehouse_quantity": product.get('hotel_warehouse_quantity', 0) or 0,
                "quantity_installed": product.get('quantity_installed', 0) or 0,
            })
        return result_products
    except Exception as e:
        print(f"Error fetching floor products data: {e}")
        return []

# Helper function to get room products data
def _get_room_products_data(room_model_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    pd.id, pd.item, pd.client_id, pd.description, pd.supplier,
                    prm.quantity AS quantity_needed_per_room,
                    COALESCE(inv.quantity_installed, 0) AS quantity_installed,
                    COALESCE(inv.quantity_available, 0) AS available_qty
                FROM product_room_model prm
                JOIN product_data pd ON prm.product_id = pd.id
                JOIN room_model rm ON prm.room_model_id = rm.id
                LEFT JOIN inventory inv ON pd.client_id = inv.client_id
                WHERE prm.room_model_id = %s
                ORDER BY pd.client_id;
            """, [room_model_id])

            columns = [col[0] for col in cursor.description]
            products = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return products
    except Exception as e:
        print(f"Error fetching room products data: {e}")
        return []

@session_login_required
def floor_products_list(request):
    floor_number = request.GET.get('floor_number', '').strip()
    product_list = []
    error_message = None

    if floor_number:
        try:
            # Validate floor_number is an integer if necessary
            floor_number = int(floor_number) # Raises ValueError if not an integer
            print("floor_number",floor_number, type(floor_number))

            product_list = _get_floor_products_data(floor_number)
            if not product_list and request.GET: # Check if it was a search attempt
                 error_message = f"No products found for floor {floor_number}."

            # Handle XLS download request
            if request.GET.get('download') == 'xls':
                if product_list:
                    filename = f"products_list_for_{floor_number}_floor.xls"
                    return _generate_xls_response(product_list, filename, sheet_name=f"Floor {floor_number}")
                else:
                     # Optionally handle download request when no data
                     messages.warning(request, f"No data to download for floor {floor_number}.")
                     # Redirect or render template again
                     return redirect(request.path_info + f'?floor_number={floor_number}')


        except ValueError:
            error_message = "Invalid floor number entered. Please enter a number."
            floor_number = '' # Clear invalid input for template rendering
        except Exception as e:
            error_message = f"An error occurred: {str(e)}"
            print(f"Error in floor_products_list view: {e}")


    context = {
        'product_list': product_list,
        'floor_number': floor_number,
        'error_message': error_message,
    }
    return render(request, 'floor_products_list.html', context)


@session_login_required
def room_number_products_list(request):
    room_number = request.GET.get('room_number', '').strip()
    product_list = []
    error_message = None
    found_room = None
    room_model_name = None

    if room_number:
        try:
            # Find the room
            found_room = RoomData.objects.select_related('room_model_id').get(room=room_number)
            room_model_id = found_room.room_model_id.id if found_room.room_model_id else None
            room_model_name = found_room.room_model_id.room_model if found_room.room_model_id else "Unknown Model"

            if room_model_id:
                product_list = _get_room_products_data(room_model_id)
                if not product_list and request.GET:
                    error_message = f"No specific products configured for room model '{room_model_name}' (used by room {room_number})."
            else:
                error_message = f"Room {room_number} does not have an associated room model."
                product_list = [] # Ensure product list is empty

            # Handle XLS download request
            if request.GET.get('download') == 'xls' and room_model_id:
                if product_list:
                    filename = f"products_list_for_room_{room_number}_{room_model_name.replace(' ', '_')}.xls"
                    return _generate_xls_response(product_list, filename, sheet_name=f"Room {room_number} ({room_model_name})")
                else:
                    messages.warning(request, f"No product data to download for room {room_number} (Model: {room_model_name}).")
                    # Redirect back to the search page for the same room number
                    return redirect(request.path_info + f'?room_number={room_number}')
            elif request.GET.get('download') == 'xls' and not room_model_id:
                 messages.warning(request, f"Cannot download product list for room {room_number} as it has no associated model.")
                 return redirect(request.path_info + f'?room_number={room_number}')


        except RoomData.DoesNotExist:
             error_message = f"Room number '{room_number}' not found."
             room_number = '' # Clear invalid input
             product_list = [] # Ensure product list is empty
        except Exception as e:
             error_message = f"An error occurred: {str(e)}"
             print(f"Error in room_number_products_list view: {e}")
             product_list = [] # Ensure product list is empty


    context = {
        'product_list': product_list,
        'room_number': room_number, # Pass back the searched room number
        'found_room': found_room,
        'room_model_name': room_model_name,
        'error_message': error_message,
    }
    return render(request, 'room_products_list.html', context)

# --- Issue Tracking Views --- 
from django.contrib.auth.models import User

@session_login_required # Corrected decorator
def issue_list(request):
    user = get_object_or_404(InvitedUser, id=request.session.get("user_id"))
    user_roles = user.role
    queryset = Issue.objects.filter(
        Q(created_by=user) |
        Q(observers=user) |
        Q(assignee=user)
    ).distinct().order_by('-created_at').select_related('created_by', 'assignee')

    # Filtering
    status = request.GET.get('status')
    if status:
        queryset = queryset.filter(status=status)
    issue_type = request.GET.get('type')
    if issue_type:
        queryset = queryset.filter(type=issue_type)
    q = request.GET.get('q')
    if q:
        queryset = queryset.filter(Q(title__icontains=q) | Q(id__icontains=q))

    # Pagination
    paginator = Paginator(queryset, 10)  # Show 10 issues per page
    page_number = request.GET.get('page')
    try:
        issues_page = paginator.page(page_number)
    except PageNotAnInteger:
        issues_page = paginator.page(1)
    except EmptyPage:
        issues_page = paginator.page(paginator.num_pages)

    # For filter dropdowns
    issue_statuses = Issue.IssueStatus.choices if hasattr(Issue, 'IssueStatus') else [
        ('OPEN', 'Open'), ('WORKING', 'Working'), ('PENDING', 'Pending'), ('CLOSE', 'Close')
    ]
    issue_types = Issue.IssueType.choices if hasattr(Issue, 'IssueType') else [
        ('ROOM', 'Room'), ('FLOOR', 'Floor'),('OTHER', 'other')
    ]

    context = {
        'issues': issues_page,
        'user_roles': user_roles,
        'is_paginated': issues_page.has_other_pages(),
        'page_obj': issues_page,
        'issue_statuses': issue_statuses,
        'issue_types': issue_types,
    }
    return render(request, 'issues/issue_list.html', context)


@session_login_required  # or your login decorator
def issue_detail(request, issue_id):
    issue = get_object_or_404(Issue, id=issue_id)
    comments = issue.comments.all().select_related('content_type')
    invited_user = get_object_or_404(InvitedUser, id=request.session.get("user_id"))

    # Build comment_data for the template
    comment_data = []
    for comment in comments:
        commenter = comment.commenter
        comment_data.append({
            "comment_id": comment.id,
            "text_content": comment.text_content,
            "media": comment.media,
            "commenter_id": getattr(commenter, "id", None),
            "commenter_name": str(commenter),
            "is_by_current_user": commenter == invited_user,
            "created_at": comment.created_at,
        })

    can_comment = (
        issue.created_by == invited_user or
        invited_user in issue.observers.all() or
        issue.assignee == invited_user
    )

    comment_form = CommentForm()

    context = {
        'issue': issue,
        'comment_data': comment_data,
        'comment_form': comment_form,
        'user': invited_user,
        'can_comment': can_comment,
        'user_roles': request.session.get('user_roles', [])
    }
    return render(request, 'issues/issue_detail.html', context)
# ... (keep issue_create, invited_user_comment_create, and other non-admin views) ...

@session_login_required
def issue_create(request):
    user = None
    invited_user_id = request.session.get("user_id")
    if invited_user_id:
        user = InvitedUser.objects.filter(id=invited_user_id).first()
    if not user and request.user.is_authenticated:
        user = InvitedUser.objects.filter(email__iexact=request.user.email).first()
    
    is_admin = False
    admin_user = None
    if not user and request.user.is_authenticated:
        # Check if this is a Django User with UserProfile and is_administrator
        try:
            if hasattr(request.user, 'profile') and request.user.profile.is_administrator:
                is_admin = True
                admin_user = request.user
        except UserProfile.DoesNotExist:
            pass

    if not user and not is_admin:
        messages.error(request, "Associated invited user account not found.")
        return redirect("issue_list")

    
    initial_data = {}
    if request.method == 'GET':
        prefill_type = request.GET.get('type')
        prefill_room_id = request.GET.get('related_rooms')
        prefill_floor = request.GET.get('related_floors')
        prefill_product_id = request.GET.get('related_product')

        if prefill_type:
            initial_data['type'] = prefill_type
            
            if prefill_type == IssueType.ROOM and prefill_room_id:
                try:
                    room_ids = [int(id) for id in prefill_room_id.split(',')]
                    if RoomData.objects.filter(pk__in=room_ids).exists():
                        initial_data['related_rooms'] = room_ids
                except (ValueError, TypeError):
                    pass
            elif prefill_type == IssueType.FLOOR and prefill_floor:
                try:
                    floor_ids = [int(id) for id in prefill_floor.split(',')]
                    initial_data['related_floors'] = floor_ids
                except (ValueError, TypeError):
                    pass
            elif prefill_type == IssueType.PRODUCT and prefill_product_id:
                try:
                    product_ids = [int(id) for id in prefill_product_id.split(',')]
                    if ProductData.objects.filter(pk__in=product_ids).exists():
                        initial_data['related_product'] = product_ids
                except (ValueError, TypeError):
                    pass

    if request.method == 'POST':
        form = IssueForm(request.POST, request.FILES)
        
        if form.is_valid():
            issue = form.save(commit=False)
            issue.created_by = user
            
            # Convert floor IDs to integers before saving
            if issue.type == IssueType.FLOOR:
                floor_ids = form.cleaned_data.get('related_floors', [])
                issue.related_floors = [int(floor_id) for floor_id in floor_ids]
            
            issue.save()
            form.save_m2m()  # Save M2M relationships
            
            # Add creator as observer
            issue.observers.add(user)

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

            # Create initial comment if there's media
            if media_urls:
                Comment.objects.create(
                    issue=issue,
                    commenter=user,
                    text_content=form.cleaned_data.get('description', ''),
                    media=media_urls
                )

            success_message = f"Issue #{issue.id} created successfully."
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'message': success_message,
                    'redirect_url': reverse('issue_detail', kwargs={'issue_id': issue.id})
                })
            else:
                messages.success(request, success_message)
                return redirect('issue_detail', issue_id=issue.id)
        else:
            logger.error(f"Form errors: {form.errors.as_json()}")
            error_message = "Please correct the errors below."
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'message': error_message,
                    'errors': json.loads(form.errors.as_json())
                }, status=400)
            else:
                messages.error(request, error_message)
                return render(request, 'issues/issue_form.html', {'form': form})
    else:
        form = IssueForm(initial=initial_data)

    return render(request, 'issues/issue_form.html', {'form': form})

@session_login_required # Uses custom session auth
def invited_user_comment_create(request, issue_id):
    invited_user = get_object_or_404(InvitedUser, id=request.session.get("user_id"))
    issue = get_object_or_404(Issue, id=issue_id)

    # Permission check: User must be creator, observer, or assignee to comment
    can_comment = (
        issue.created_by == invited_user or
        invited_user in issue.observers.all() or
        issue.assignee == invited_user
    )

    if not can_comment:
        messages.error(request, "You do not have permission to comment on this issue.")
        return redirect('issue_detail', issue_id=issue.id)

    if request.method == 'POST':
        form = CommentForm(request.POST, request.FILES)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.commenter = invited_user # Assign the InvitedUser instance
            comment.issue = issue

            media_info = []
            images = form.cleaned_data.get('images', [])
            video = form.cleaned_data.get('video')

            for img in images:
                if img.size > 4 * 1024 * 1024: # 4MB
                    messages.error(request, f"Image '{img.name}' exceeds 4MB limit.")
                    continue # Skip this file
                file_name = default_storage.save(f"issues/comments/user/{issue.id}/{uuid.uuid4()}_{img.name}", img)
                media_info.append({"type": "image", "url": default_storage.url(file_name), "name": img.name, "size": img.size})
            
            if video:
                if video.size > 100 * 1024 * 1024: # 100MB
                    messages.error(request, f"Video '{video.name}' exceeds 100MB limit.")
                else:
                    file_name = default_storage.save(f"issues/comments/user/{issue.id}/{uuid.uuid4()}_{video.name}", video)
                    media_info.append({"type": "video", "url": default_storage.url(file_name), "name": video.name, "size": video.size})

            comment.media = media_info
            comment.save()
            messages.success(request, "Your comment has been added.")
            return redirect('issue_detail', issue_id=issue.id) # Redirect to standard issue detail
        else:
            messages.error(request, "There was an error with your comment. Please check the details.")

            comments = issue.comments.all().select_related('content_type')
            for c in comments: # Pre-fetch commenter
                _ = c.commenter
            return render(request, 'issues/issue_detail.html', {
                'issue': issue,
                'comments': comments,
                'comment_form': form, # Pass the invalid form back
                'user': request.user, # Or invited_user if more appropriate for template
                'can_comment': can_comment # Pass the permission status, which must be True to reach here
            })
    else:
        # GET request usually means the form is displayed on the issue_detail page
        return redirect('issue_detail', issue_id=issue.id)

@login_required
def get_room_products(request):
    room_number = request.GET.get('room_number')
    installation_id = request.GET.get('installation_id')

    if not room_number:
        return JsonResponse({'error': 'Room number is required'}, status=400)

    if not installation_id:
        return JsonResponse({'error': 'Installation ID is required'}, status=400)

    try:
        with connection.cursor() as cursor:
            # Step 1: Get room_id and room_model_id
            cursor.execute("""
                SELECT rd.id as room_id, rm.id as room_model_id
                FROM room_data rd
                LEFT JOIN room_model rm ON rd.room_model_id = rm.id
                WHERE rd.room = %s
            """, [room_number])

            room_data = cursor.fetchone()
            if not room_data:
                return JsonResponse({'error': 'Room not found'}, status=404)

            room_id, room_model_id = room_data

            if not room_model_id:
                return JsonResponse({'error': 'No room model found for this room'}, status=404)

            # Step 2: Check if install_detail has entries
            cursor.execute("""
                SELECT 
                    id.product_id as id,
                    pd.item as name,
                    pd.description,
                    id.status,
                    id.installed_on,
                    u.name as installed_by
                FROM install_detail id
                JOIN product_data pd ON id.product_id = pd.id
                LEFT JOIN invited_users u ON id.installed_by = u.id
                WHERE id.room_id = %s AND id.installation_id = %s
                ORDER BY pd.item
            """, [room_id, installation_id])

            install_entries = cursor.fetchall()
            columns = [col[0] for col in cursor.description]

            if install_entries:
                products = []
                for row in install_entries:
                    product = dict(zip(columns, row))
                    if product['installed_on']:
                        product['installed_on'] = product['installed_on'].strftime('%Y-%m-%d')
                    products.append(product)

                return JsonResponse({'success': True, 'source': 'install_detail', 'products': products})

            # Step 3: Fallback to dynamic room model-based product lookup
            cursor.execute("""
                SELECT 
                    pd.id,
                    pd.item as name,
                    pd.description,
                    prm.quantity,
                    COALESCE(id.status, 'NO') as status,
                    id.installed_on,
                    u.name as installed_by
                FROM product_room_model prm
                JOIN product_data pd ON prm.product_id = pd.id
                LEFT JOIN install_detail id ON pd.id = id.product_id
                                           AND id.installation_id = %s
                                           AND id.room_id = %s
                LEFT JOIN invited_users u ON id.installed_by = u.id
                WHERE prm.room_model_id = %s
                ORDER BY pd.item
            """, [installation_id, room_id, room_model_id])

            dynamic_products = []
            columns = [col[0] for col in cursor.description]

            for row in cursor.fetchall():
                product = dict(zip(columns, row))
                if product['installed_on']:
                    product['installed_on'] = product['installed_on'].strftime('%Y-%m-%d')
                dynamic_products.append(product)

            return JsonResponse({'success': True, 'source': 'room_model_fallback', 'products': dynamic_products})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required # Standard Django login for admin views
def admin_get_installation_details(request):
    if not request.user.is_authenticated or not request.user.is_staff: # Basic permission check
        return JsonResponse({"success": False, "message": "Unauthorized"}, status=403)

    installation_id_str = request.GET.get("installation_id")
    room_number_str = request.GET.get("room_number")

    # Determine current user's name for JS prefill
    current_user_name = request.user.username # Default to username
    # Check if the logged-in user is an instance of InvitedUser and has a 'name' attribute
    # This depends on your authentication setup for admins.
    # If admins are Django users, request.user.name might not exist unless custom user model.
    # If admins are InvitedUser and session is set up, request.user could be InvitedUser.
    if hasattr(request.user, 'name') and request.user.name and isinstance(request.user, InvitedUser):
        current_user_name = request.user.name
    elif hasattr(request.user, 'get_full_name') and request.user.get_full_name():
        current_user_name = request.user.get_full_name()


    if installation_id_str: #优先处理编辑模式 (Prioritize edit mode if installation_id is present)
        try:
            installation_id = int(installation_id_str)
            # Fetch the specific installation
            installation = get_object_or_404(Installation, id=installation_id)
            # Get room number from the installation record
            room_num_for_helper = str(installation.room)

            data = _get_installation_checklist_data(room_number=room_num_for_helper, installation_id=installation_id)
            if data["success"]:
                data["current_user_name"] = current_user_name
            return JsonResponse(data)
        except ValueError:
            return JsonResponse({"success": False, "message": "Invalid Installation ID format."}, status=400)
        # Installation.DoesNotExist is already handled by get_object_or_404
        except Exception as e:
            logger.error(f"Error in admin_get_installation_details (edit mode) for install_id {installation_id_str}: {e}", exc_info=True)
            return JsonResponse({"success": False, "message": f"An unexpected server error occurred: {str(e)}"}, status=500)

    elif room_number_str: # 如果没有 installation_id，但有 room_number，则为创建模式加载 (If no installation_id, but room_number is present, load for create mode)
        try:
            # Validate room_number can be converted to int for RoomData query if necessary,
            # _get_installation_checklist_data expects room_number as string but might do internal conversion.
            # For create mode, installation_id is None.
            data = _get_installation_checklist_data(room_number=room_number_str, installation_id=None)
            if data["success"]:
                data["current_user_name"] = current_user_name
            else:
                # If _get_installation_checklist_data itself returns success:False, pass its message
                return JsonResponse(data, status=400 if data.get("message") else 500)
            return JsonResponse(data)
        except RoomData.DoesNotExist: # Explicitly catch if _get_installation_checklist_data can't find room
             return JsonResponse({"success": False, "message": f"Room {room_number_str} not found. Cannot create installation checklist."}, status=404)
        except ValueError: # e.g. if room_number_str is not a valid int and RoomData.room is int
            return JsonResponse({"success": False, "message": "Invalid Room Number format provided."}, status=400)
        except Exception as e:
            logger.error(f"Error in admin_get_installation_details (create mode) for room {room_number_str}: {e}", exc_info=True)
            return JsonResponse({"success": False, "message": f"An unexpected server error occurred: {str(e)}"}, status=500)
    else:
        return JsonResponse({"success": False, "message": "Either Installation ID (for edit) or Room Number (for create) is required."}, status=400)

@login_required # Standard Django login for admin views
@csrf_exempt # If using AJAX POST from admin template that might not embed CSRF token in form data easily initially
def admin_save_installation_details(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({"success": False, "message": "Unauthorized"}, status=403)

    if request.method == "POST":
        installation_id_str = request.POST.get("installation_id")
        
        if not installation_id_str:
            return JsonResponse({"success": False, "message": "Installation ID is required in POST data."}, status=400)

        try:
            installation_id = int(installation_id_str)
            # Fetch the installation to get the room number for the helper
            installation_instance = get_object_or_404(Installation, id=installation_id)
            room_number_str = str(installation_instance.room) # Get room number from the instance

            # Determine the user instance for 'checked_by' fields.
            # If your admins are regular Django Users:
            # admin_user_instance = request.user
            # If your admins are also InvitedUser instances and logged in via session:
            # For simplicity, let's assume if an admin is doing this, they are the 'user_instance'
            # This might need refinement based on how admin identity is passed or if it should be logged differently.
            
            # For now, let's try to find an InvitedUser that matches the logged-in Django admin user's email.
            # This is a common pattern if you have two user systems.
            # Or, if admins are *always* also InvitedUsers and logged in through the custom session:
            admin_user_as_invited_user = None
            if hasattr(request.user, 'email'): # Check if the Django user has an email
                 admin_user_as_invited_user = InvitedUser.objects.filter(email=request.user.email).first()

            if not admin_user_as_invited_user:
                 # Fallback or error if no matching InvitedUser.
                 # For now, as a simple approach, create a placeholder or use a default admin InvitedUser if one exists.
                 # This part depends on your user management strategy for admins.
                 # Let's assume for now the logged-in Django user *is* the one making changes.
                 # The _save_installation_data expects an InvitedUser instance.
                 # If your request.user *is* an InvitedUser due to middleware, this is simpler.
                 # Given the mix of @login_required and @session_login_required, this needs clarity.
                 # Assuming @login_required means a Django user.
                 # We need an InvitedUser to pass to the helper.
                 
                 # If session_login_required was used for admins, then:
                 # invited_user_id = request.session.get("user_id")
                 # user_instance_for_saving = get_object_or_404(InvitedUser, id=invited_user_id)

                 # If @login_required (Django auth) is used for admins:
                 # We need a way to map Django User to InvitedUser for the save helper.
                 # Simplest for now: if a field 'name' on Django User matches an InvitedUser.name or email.
                 # This is a placeholder for robust user mapping.
                # Default to first admin if any, or handle error
                # This is a TEMPORARY HACK - replace with proper admin user (InvitedUser) retrieval
                user_instance_for_saving = InvitedUser.objects.filter(role__contains=['admin']).first()
                if not user_instance_for_saving and InvitedUser.objects.exists(): # fallback to any user if no admin
                    user_instance_for_saving = InvitedUser.objects.first()
                elif not InvitedUser.objects.exists():
                     return JsonResponse({"success": False, "message": "Configuration error: No InvitedUser available to attribute changes."}, status=500)

                logger.warning(f"Admin save: Using fallback InvitedUser '{user_instance_for_saving.name}' for changes by Django user '{request.user.username}'. Review user mapping.")

            else: # Found a matching InvitedUser for the Django admin
                user_instance_for_saving = admin_user_as_invited_user


            result = _save_installation_data(request.POST, user_instance_for_saving, room_number_str, installation_id_str)
            
            if result["success"]:
                return JsonResponse(result)
            else:
                return JsonResponse(result, status=400) # Or 500 if server-side issue in helper

        except ValueError:
            return JsonResponse({"success": False, "message": "Invalid Installation ID format."}, status=400)
        except Installation.DoesNotExist:
            return JsonResponse({"success": False, "message": "Installation record not found to save against."}, status=404)
        except Exception as e:
            logger.error(f"Critical error in admin_save_installation_details for install_id {installation_id_str}: {e}", exc_info=True)
            return JsonResponse({"success": False, "message": f"An server error occurred: {str(e)}"}, status=500)

    return JsonResponse({"error": "Invalid request method. Only POST is allowed."}, status=405)

@session_login_required
def get_shipment_details(request):
    shipment_id = request.GET.get('shipment_id')
    if not shipment_id:
        return JsonResponse({'success': False, 'message': 'Shipment ID is required'})
    
    try:
        # Get the reference shipping item
        reference_item = get_object_or_404(Shipping, id=shipment_id)
        container_id = reference_item.bol
        
        # Get all items with this container ID
        items = Shipping.objects.filter(bol=container_id)
        
        # Format dates for form fields
        ship_date = reference_item.ship_date.strftime('%Y-%m-%d') if reference_item.ship_date else ''
        expected_arrival = reference_item.expected_arrival_date.strftime('%Y-%m-%d') if reference_item.expected_arrival_date else ''
        
        # Format items for JSON response
        item_list = []
        for item in items:
            # Get product name from ProductData if available
            try:
                product = ProductData.objects.get(client_id=item.client_id)
                product_name = product.description
            except ProductData.DoesNotExist:
                product_name = item.item or "Unknown Product"
            
            item_list.append({
                'id': item.id,
                'client_id': item.client_id,
                'product_name': product_name,
                'supplier': item.supplier or "N/A",
                'quantity': item.ship_qty or 1
            })
        
        return JsonResponse({
            'success': True,
            'container_id': container_id,
            'ship_date': ship_date,
            'expected_arrival': expected_arrival,
            'items': item_list
        })
    
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})

@session_login_required
def check_container_exists(request):
    """
    API endpoint to check if a container ID already exists
    """
    container_id = request.GET.get('container_id', '').strip()
    
    if not container_id:
        return JsonResponse({
            'exists': False,
            'count': 0
        })
    
    # Count shipping entries with this container ID
    count = Shipping.objects.filter(bol__iexact=container_id).count()
    
    return JsonResponse({
        'exists': count > 0,
        'count': count
    })

@session_login_required
def get_container_data(request):
    """
    API endpoint to get all data for a specific container ID
    """
    container_id = request.GET.get('container_id', '').strip()
    if not container_id:
        return JsonResponse({
            'success': False,
            'message': 'Container ID is required'
        })
    container_id_lower = container_id.lower()
    try:
        # Get all items with this container ID (case-insensitive)
        items = Shipping.objects.filter(bol__iexact=container_id_lower)
        
        if not items.exists():
            return JsonResponse({
                'success': False,
                'message': f'No items found for container ID: {container_id}'
            })
        
        # Get the first item to extract common data
        first_item = items.first()
        
        # Format dates for form fields
        ship_date = first_item.ship_date.strftime('%Y-%m-%d') if first_item.ship_date else ''
        expected_arrival = first_item.expected_arrival_date.strftime('%Y-%m-%d') if first_item.expected_arrival_date else ''
        
        # Format items for JSON response
        item_list = []
        for item in items:
            # Get product name from ProductData if available
            try:
                # Use case-insensitive lookup to match product data
                product = ProductData.objects.get(client_id__iexact=item.client_id)
                # Use description consistently (or fall back to item) - same as get_product_item_num
                product_name = product.description or product.item or "Unknown Product"
                print(f"get_container_data - client_id: {item.client_id}, product_name: {product_name}")
            except ProductData.DoesNotExist:
                product_name = item.item or "Unknown Product"
                print(f"Product not found for client_id: {item.client_id}, using item name: {product_name}")
            
            item_list.append({
                'id': item.id,
                'client_id': item.client_id,
                'product_name': product_name,
                'supplier': item.supplier or "N/A",
                'quantity': item.ship_qty or 1
            })
        
        return JsonResponse({
            'success': True,
            'container_id': container_id,
            'ship_date': ship_date,
            'expected_arrival': expected_arrival,
            'items': item_list,
            'count': len(item_list)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        })

@session_login_required
def get_container_received_items(request):
    """API endpoint to get all received items for a specific container ID"""
    container_id = request.GET.get('container_id')
    user_id = request.session.get("user_id")
    if not container_id:
        return JsonResponse({'success': False, 'message': 'Container ID is required'})
    container_id_lower = container_id.lower()
    try:
        # Debug info
        print(f"Fetching received items for container_id: {container_id_lower}")
        # Get the current user
        user = None
        if user_id:
            user = InvitedUser.objects.get(id=user_id)
        # Get all inventory received records for this container AND this user (case-insensitive)
        query = Q(container_id__iexact=container_id_lower)
        # Add user filter if available
        received_items = InventoryReceived.objects
        if user:
            received_items = received_items.filter(checked_by=user)
        # Apply the container query
        received_items = received_items.filter(query).order_by('client_id')
            
        if not received_items.exists():
            return JsonResponse({'success': False, 'message': f'No received items found for container {container_id}'})
        
        # Get the received date from the first item
        first_item = received_items.first()
        received_date = first_item.received_date.strftime('%Y-%m-%d') if first_item.received_date else ''
        
        # Format items for response
        items_data = []
        for item in received_items:
            # Get product name from ProductData if available
            try:
                product = ProductData.objects.get(client_id__iexact=item.client_id)
                product_name = product.description or product.item or item.item
            except ProductData.DoesNotExist:
                product_name = item.item or "Unknown Product"
                
            items_data.append({
                'id': item.id,
                'client_id': item.client_id,
                'product_name': product_name,
                'received_qty': item.received_qty,
                'damaged_qty': item.damaged_qty,
                'checked_by': item.checked_by.name if item.checked_by else 'Unknown'
            })
        
        print(f"Found {len(items_data)} received items")
        
        return JsonResponse({
            'success': True,
            'container_id': container_id,
            'items': items_data,
            'received_date': received_date
        })
    
    except Exception as e:
        print(f"Error in get_container_received_items: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'message': str(e)})

@session_login_required
def warehouse_receiver(request):
    """
    View for receiving warehouse shipments.
    """
    # Only update inventory quantities when submitting form, not on every page load
    
    user_id = request.session.get("user_id")
    user = None
    
    if user_id:
        try:
            user = InvitedUser.objects.get(id=user_id)
        except InvitedUser.DoesNotExist:
            pass
    
    if request.method == "POST":
        try:
            # Get form data
            reference_id = request.POST.get("reference_id", "").strip().lower()
            received_date_str = request.POST.get("received_date")
            
            # Get edit mode flags
            is_editing = request.POST.get("is_edit_mode") == "1"
            original_reference_id = request.POST.get("original_reference_id", "").strip().lower()
            delete_previous_items = request.POST.get("delete_previous_items") == "1"
            
            # Log edit mode information
            print(f"Processing form - is_editing: {is_editing}, original_reference_id: {original_reference_id}, delete_previous: {delete_previous_items}")
            
            # Parse the received date
            received_date = parse_date(received_date_str) if received_date_str else now().date()
            
            # Get the arrays of data
            client_items = request.POST.getlist("client_items[]")
            quantities = request.POST.getlist("quantities[]")  # Now these are received quantities
            damaged_quantities = request.POST.getlist("damaged_quantities[]")  # New damaged quantities field
            product_names = request.POST.getlist("product_names[]")
            
            # Validate input
            if not reference_id:
                messages.error(request, "Reference ID is required")
                return redirect("warehouse_receiver")
            
            if not client_items:
                messages.error(request, "No items added to receipt")
                return redirect("warehouse_receiver")
            
            # Check if we're in edit mode
            if is_editing and original_reference_id:
                # If editing and the reference ID has changed, make sure the new one doesn't exist
                if reference_id != original_reference_id and HotelWarehouse.objects.filter(reference_id__iexact=reference_id).exists():
                    messages.error(request, f"Cannot change reference ID to '{reference_id}' as it already exists")
                    return redirect("warehouse_receiver")
                
                # Delete previous items if requested
                if delete_previous_items:
                    deleted_count = HotelWarehouse.objects.filter(reference_id__iexact=original_reference_id).delete()[0]
                    print(f"Deleted {deleted_count} previous items for reference ID '{original_reference_id}'")
            
            # Create new warehouse receipt entries
            for i, client_item in enumerate(client_items):
                received_qty = int(quantities[i]) if i < len(quantities) and quantities[i] else 0
                damaged_qty = int(damaged_quantities[i]) if i < len(damaged_quantities) and damaged_quantities[i] else 0
                
                # Skip if received quantity is 0
                if received_qty == 0 and damaged_qty == 0:
                    continue
                
                try:
                    # Create the warehouse receipt
                    HotelWarehouse.objects.create(
                        reference_id=reference_id,
                        client_item=client_item,
                        quantity_received=received_qty,
                        damaged_qty=damaged_qty,  # Add the damaged quantity
                        checked_by=user,
                        received_date=received_date
                    )
                    # Use the recalculate function which handles case insensitivity
                    recalculate_hotel_warehouse_quantity(client_item)
                    print(f"Updated hotel warehouse quantity for {client_item} using recalculate_hotel_warehouse_quantity")
                except Exception as item_error:
                    print(f"Error creating warehouse receipt for {client_item}: {str(item_error)}")
                    # Don't raise the exception as we want to continue with other items
            
            try:
                # Call functions to update inventory quantities
                update_inventory_warehouse_quantities()
                update_inventory_hotel_warehouse_quantities()  # Also update received and damaged quantities
            except Exception as update_error:
                print(f"Error updating inventory quantities: {str(update_error)}")
                # Don't raise the exception as we want to continue with the response
            
            if is_editing:
                messages.success(request, "Warehouse receipt updated successfully")
            else:
                messages.success(request, "Warehouse receipt created successfully")
            
            return redirect("warehouse_receiver")
        
        except Exception as e:
            print(f"Error in warehouse_receiver: {e}")
            messages.error(request, f"Error: {str(e)}")
            return redirect("warehouse_receiver")
    
    return render(request, "warehouse_receiver.html", {"user_name": user.name if user else ""})

@session_login_required
def get_warehouse_receipt_details(request):
    """
    API endpoint to get details of a specific warehouse receipt
    """
    # Try to get the reference ID (which is what we want to filter by)
    # The frontend might send either receipt_id or reference_id
    reference_id = (request.GET.get('receipt_id') or request.GET.get('reference_id') or '').strip().lower()
    
    print(f"get_warehouse_receipt_details - received reference_id: {reference_id}")
    
    if not reference_id:
        return JsonResponse({'success': False, 'message': 'Reference ID is required'})
    
    try:
        # Get all items with this reference ID
        items = HotelWarehouse.objects.filter(reference_id__iexact=reference_id)
        itemsqty = WarehouseShipment.objects.filter(reference_id__iexact=reference_id)
        
        if not items.exists():
            return JsonResponse({'success': False, 'message': 'Receipt not found'})
        
        # Format receipt data
        receipt_data = {
            'id': reference_id,
            'reference_id': reference_id,
            'received_date': now().strftime('%Y-%m-%d'),  # Default to current date
            'received_by': "System"  # Default value
        }
        
        # Get the first item to get more details
        first_item = items.first()
        if first_item:
            # Get user name if available
            if first_item.checked_by:
                receipt_data['received_by'] = first_item.checked_by.name
            
            # Get received date if available
            if first_item.received_date:
                receipt_data['received_date'] = first_item.received_date.strftime('%Y-%m-%d')
        
        # Format items data
        items_data = []
        for item in items:
            # Get product name from ProductData or Inventory tables
            product_name = "Unknown Product"
            try:
                # First try to get from ProductData by client_id
                product = ProductData.objects.filter(client_id__iexact=item.client_item).first()
                if product and product.description:
                    product_name = product.description
                elif product and product.item:
                    product_name = product.item
                else:
                    # If not found, try to get from Inventory
                    inventory = Inventory.objects.filter(client_id__iexact=item.client_item).first()
                    if inventory and inventory.item:
                        # Try to get the description from ProductData using item
                        prod_from_item = ProductData.objects.filter(item__iexact=inventory.item).first()
                        if prod_from_item and prod_from_item.description:
                            product_name = prod_from_item.description
                        else:
                            product_name = inventory.item
            except Exception as e:
                logger.warning(f"Error looking up product name for {item.client_item}: {e}")
            
            # Find the matching shipment item for this warehouse item
            matching_shipment = itemsqty.filter(client_id__iexact=item.client_item).first()
            ship_qty = matching_shipment.ship_qty if matching_shipment else 0
            
            items_data.append({
                'id': item.id,
                'client_item': item.client_item,  # Use consistent field name
                'product_name': product_name,
                'ship_qty': ship_qty,  # Use the ship_qty from the matching shipment
                'quantity_received': item.quantity_received,  # Use field name from the model
                'damaged_qty': item.damaged_qty  # Add damaged quantity
            })
        
        return JsonResponse({
            'success': True,
            'receipt': receipt_data,
            'items': items_data
        })
        
    except Exception as e:
        logger.error(f"Error in get_warehouse_receipt_details: {e}", exc_info=True)
        return JsonResponse({'success': False, 'message': str(e)})

def update_inventory_warehouse_quantities():
    """
    Update all inventory hotel_warehouse_quantity values based on the sum of quantities in HotelWarehouse.
    Uses case-insensitive matching for client IDs.
    """
    try:
        # Get all unique client items from Inventory
        inventory_items = Inventory.objects.values_list('client_id', flat=True).distinct()
        updated_count = 0
        
        for inv_client_id in inventory_items:
            try:
                # Find all case variations of this client_id in HotelWarehouse
                warehouse_items = HotelWarehouse.objects.filter(
                    client_item__iexact=inv_client_id
                )
                
                if warehouse_items.exists():
                    # Calculate total quantity for all case variations
                    total_qty = warehouse_items.aggregate(
                        total=Sum('quantity_received')
                    )['total'] or 0
                    
                    # Update inventory record
                    Inventory.objects.filter(client_id=inv_client_id).update(hotel_warehouse_quantity=total_qty)
                    updated_count += 1
                else:
                    # If no warehouse items found for this inventory item, set quantity to 0
                    Inventory.objects.filter(client_id=inv_client_id).update(hotel_warehouse_quantity=0)
            except Exception as item_error:
                logger.error(f"Error updating warehouse quantity for {inv_client_id}: {item_error}")
        
        # Also update received_at_hotel_quantity and damaged_quantity_at_hotel
        # Call our dedicated function for those fields
        update_inventory_hotel_warehouse_quantities()
        
        logger.info(f"Updated hotel_warehouse_quantity for {updated_count} inventory items")
    except Exception as e:
        logger.error(f"Error updating inventory warehouse quantities: {e}", exc_info=True)

@session_login_required
def warehouse_request_items(request):
    """
    API endpoint to get pending warehouse request items for a specific floor
    """
    floor_number = request.GET.get('floor_number')
    
    if not floor_number:
        return JsonResponse({'success': False, 'message': 'Floor number is required'})
    
    try:
        # Get only pending warehouse request items for this floor
        items = WarehouseRequest.objects.filter(floor_number=floor_number, sent=False)
        
        if not items.exists():
            return JsonResponse({'success': False, 'message': 'No items found for this floor'})
        
        # Format items for response
        items_data = []
        for item in items:
            # Get product name from ProductData or Inventory tables
            product_name = "Unknown Product"
            try:
                # First try to get from ProductData by client_id
                product = ProductData.objects.filter(client_id__iexact=item.client_item).first()
                if product and product.description:
                    product_name = product.description
                elif product and product.item:
                    product_name = product.item
                else:
                    # If not found, try to get from Inventory
                    inventory = Inventory.objects.filter(client_id__iexact=item.client_item).first()
                    if inventory and inventory.item:
                        # Try to get the description from ProductData using item
                        prod_from_item = ProductData.objects.filter(item__iexact=inventory.item).first()
                        if prod_from_item and prod_from_item.description:
                            product_name = prod_from_item.description
                        else:
                            product_name = inventory.item
            except Exception as e:
                logger.warning(f"Error looking up product name for {item.client_item}: {e}")
            
            items_data.append({
                'id': item.id,  # Include the specific item ID for accurate updates
                'client_item': item.client_item,
                'product_name': product_name,
                'quantity_requested': item.quantity_requested,
                'quantity_sent': item.quantity_sent,
                'sent': True if item.sent else False
            })
        
        return JsonResponse({
            'success': True,
            'items': items_data
        })
        
    except Exception as e:
        logger.error(f"Error in warehouse_request_items: {e}", exc_info=True)
        return JsonResponse({'success': False, 'message': str(e)})

@session_login_required
def get_previous_warehouse_requests(request):
    """
    API endpoint to get all previous warehouse requests for a specific floor
    """
    floor_number = request.GET.get('floor_number')
    
    if not floor_number:
        return JsonResponse({'success': False, 'message': 'Floor number is required'})
    
    try:
        # Get all warehouse request items for this floor
        items = WarehouseRequest.objects.filter(floor_number=floor_number)
        
        if not items.exists():
            return JsonResponse({'success': False, 'message': 'No items found for this floor'})
        
        # Format items for response
        items_data = []
        for item in items:
            # Get product name from ProductData or Inventory tables
            product_name = "Unknown Product"
            try:
                # First try to get from ProductData by client_id
                product = ProductData.objects.filter(client_id__iexact=item.client_item).first()
                if product and product.description:
                    product_name = product.description
                elif product and product.item:
                    product_name = product.item
                else:
                    # If not found, try to get from Inventory
                    inventory = Inventory.objects.filter(client_id__iexact=item.client_item).first()
                    if inventory and inventory.item:
                        # Try to get the description from ProductData using item
                        prod_from_item = ProductData.objects.filter(item__iexact=inventory.item).first()
                        if prod_from_item and prod_from_item.description:
                            product_name = prod_from_item.description
                        else:
                            product_name = inventory.item
            except Exception as e:
                logger.warning(f"Error looking up product name for {item.client_item}: {e}")
            
            # Get requested_by, sent_by and received_by names
            requested_by_name = item.requested_by.name if item.requested_by else "Unknown"
            received_by_name = item.received_by.name if item.received_by else "Not received"
            
            # Explicitly debug sent_by and sent_date values
            print(f"Item {item.id} sent_by_id={item.sent_by_id}, sent_date={item.sent_date}")
            
            # Get sent_by name (if exists)
            sent_by_name = "Not set"
            if item.sent_by_id:
                try:
                    sent_by_user = InvitedUser.objects.get(id=item.sent_by_id)
                    sent_by_name = sent_by_user.name
                except:
                    sent_by_name = f"User ID {item.sent_by_id}"
            
            # Format the sent date if available
            sent_date_str = item.sent_date.strftime("%Y-%m-%d") if item.sent_date else "Not set"
            
            print(f"Item {item.id} sent_by_name={sent_by_name}, sent_date_str={sent_date_str}")
            
            items_data.append({
                'id': item.id,
                'client_item': item.client_item,
                'product_name': product_name,
                'quantity_requested': item.quantity_requested,
                'quantity_sent': item.quantity_sent,
                'quantity_received': item.quantity_received,
                'requested_by': requested_by_name,
                'sent_by': sent_by_name,
                'sent_date': sent_date_str,
                'received_by': received_by_name,
                'sent': item.sent
            })
        
        return JsonResponse({
            'success': True,
            'items': items_data
        })
        
    except Exception as e:
        logger.error(f"Error in get_previous_warehouse_requests: {e}", exc_info=True)
        return JsonResponse({'success': False, 'message': str(e)})

@session_login_required
def get_warehouse_receipts(request):
    """
    API endpoint to get paginated warehouse receipts for AJAX loading
    """
    page = request.GET.get('page', 1)
    
    try:
        # More efficient query: Get the most recent receipts first with date ordering
        recent_receipts = (
            HotelWarehouse.objects
            .values('reference_id')
            .annotate(
                latest_date=Max('received_date'),
                items_count=Count('id'),
                total_quantity=Sum('quantity_received'),
                first_id=Min('id')
            )
            .order_by('-latest_date')
        )
        
        # Get the prefetched items in a single query
        receipt_ids = [r['first_id'] for r in recent_receipts]
        receipt_items = HotelWarehouse.objects.filter(id__in=receipt_ids).select_related('checked_by')
        receipt_dict = {item.id: item for item in receipt_items}
        
        # Process the results without additional queries
        receipts_list = []
        for receipt in recent_receipts:
            ref_id = receipt['reference_id']
            first_item = receipt_dict.get(receipt['first_id'])
            
            if first_item:
                # Get received date
                received_date = receipt['latest_date'].strftime('%Y-%m-%d') if receipt['latest_date'] else now().strftime('%Y-%m-%d')
                
                # Get user name if available
                received_by = "System"  # Default value
                if first_item.checked_by:
                    received_by = first_item.checked_by.name
                
                receipts_list.append({
                    'id': ref_id,  # Use reference_id as the ID for the receipt
                    'reference_id': ref_id,
                    'received_date': received_date,
                    'items_count': receipt['items_count'],
                    'total_quantity': receipt['total_quantity'] or 0,
                    'received_by': received_by
                })
        
        # Create paginator
        paginator = Paginator(receipts_list, 10)  # 10 receipts per page
        
        try:
            receipts_page = paginator.page(page)
        except PageNotAnInteger:
            receipts_page = paginator.page(1)
        except EmptyPage:
            receipts_page = paginator.page(paginator.num_pages)
        
        # Return paginated data as JSON
        return JsonResponse({
            'success': True,
            'receipts': list(receipts_page),
            'page': receipts_page.number,
            'total_pages': paginator.num_pages,
            'has_previous': receipts_page.has_previous(),
            'previous_page': receipts_page.previous_page_number() if receipts_page.has_previous() else None,
            'has_next': receipts_page.has_next(),
            'next_page': receipts_page.next_page_number() if receipts_page.has_next() else None
        })
        
    except Exception as e:
        logger.error(f"Error in get_warehouse_receipts: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': str(e)
        })

@session_login_required
def warehouse_shipment(request):
    user_id = request.session.get("user_id")
    user_name = ""

    # Check if there was an abandoned edit that needs cleanup
    abandoned_edit_id = request.session.get("editing_warehouse_shipment")
    if abandoned_edit_id:
        # Clear the session flag first to prevent loops
        del request.session["editing_warehouse_shipment"]
        request.session.save()
        
        try:
            # Revert inventory for this abandoned edit
            items = WarehouseShipment.objects.filter(reference_id__iexact=abandoned_edit_id)
            if items.exists():
                print(f"Found abandoned edit for {abandoned_edit_id}, reverting inventory changes")
                for item in items:
                    client_id = item.client_id
                    ship_qty = item.ship_qty
                    
                    # Find inventory item
                    inventory_item = Inventory.objects.filter(client_id__iexact=client_id).first()
                    if inventory_item and inventory_item.quantity_available is not None:
                        # Subtract back the quantity, ensuring we don't go negative
                        if inventory_item.quantity_available >= ship_qty:
                            inventory_item.quantity_available -= ship_qty
                        else:
                            # If not enough available, just set to 0
                            inventory_item.quantity_available = 0
                        
                        inventory_item.save()
                        print(f"Reverted inventory for {client_id}, new available: {inventory_item.quantity_available}")
        except Exception as e:
            print(f"Error reverting abandoned edit: {str(e)}")

    if user_id:
        try:
            user = InvitedUser.objects.get(id=user_id)
            user_name = user.name
        except InvitedUser.DoesNotExist:
            pass

    if request.method == "POST":
        try:
            # Get common form fields
            ship_date_str = request.POST.get("ship_date")
            expected_arrival_date_str = request.POST.get("expected_arrival_date")
            tracking_info = request.POST.get("tracking_info")
            
            # Check if this is an edit operation
            is_editing = request.POST.get("is_editing") == "1"
            editing_reference_id = request.POST.get("editing_reference_id", "").strip()
            
            # Debug logging
            print(f"Form submission - is_editing value: '{request.POST.get('is_editing')}'")
            print(f"Form submission - is_editing: {is_editing}, editing_reference_id: '{editing_reference_id}'")
            print(f"Current tracking_info (Reference ID): '{tracking_info}'")
            
            # For editing, temporarily restore the original quantities back to inventory
            # This ensures we can validate against the correct available quantity
            if is_editing and editing_reference_id:
                # Get all existing items for this reference ID
                existing_items = WarehouseShipment.objects.filter(reference_id__iexact=editing_reference_id)
                print(f"Found {existing_items.count()} existing items for reference_id '{editing_reference_id}'")
                
                # Restore quantities back to inventory
                for existing_item in existing_items:
                    client_id = existing_item.client_id
                    ship_qty = existing_item.ship_qty
                    
                    # Find inventory item
                    inventory_item = Inventory.objects.filter(client_id__iexact=client_id).first()
                    if inventory_item and inventory_item.quantity_available is not None:
                        # Add back the original quantity
                        inventory_item.quantity_available += ship_qty
                        # Use direct SQL update to bypass signals
                        from django.db import connection
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "UPDATE inventory SET quantity_available = %s WHERE id = %s",
                                [inventory_item.quantity_available, inventory_item.id]
                            )
                        print(f"Restored {ship_qty} to inventory for {client_id}, new available: {inventory_item.quantity_available}")
            
            # Parse dates
            if ship_date_str:
                ship_date = make_aware(datetime.strptime(ship_date_str, "%Y-%m-%d"))
            else:
                ship_date = None
            
            if expected_arrival_date_str:
                expected_arrival_date = make_aware(datetime.strptime(expected_arrival_date_str, "%Y-%m-%d"))
            else:
                expected_arrival_date = None
                
            # Get multiple items data
            client_items = request.POST.getlist("client_items")
            product_names = request.POST.getlist("product_names")
            quantities = request.POST.getlist("quantities")
            item_ids = request.POST.getlist("item_ids")  # Get the IDs of existing items when editing
            
            # Debug logging
            print(f"Items to process: {len(client_items)}")
            print(f"Client items: {client_items}")
            print(f"Item IDs: {item_ids}")
            
            # Check if we have items to process
            if not client_items:
                messages.error(request, "No items added to shipment.")
                return redirect("warehouse_shipment")
            
            # Check if this reference ID already exists (regardless of edit flag)
            container_exists = False
            if tracking_info and not is_editing:
                existing_count = WarehouseShipment.objects.filter(reference_id=tracking_info).count()
                if existing_count > 0:
                    container_exists = True
                    print(f"Reference ID '{tracking_info}' already exists with {existing_count} items")
                    messages.error(request, f"Reference ID '{tracking_info}' already exists. Please use a different ID or edit the existing one.")
                    return redirect("warehouse_shipment")
            
            # Handle editing differently - don't delete and recreate
            if is_editing and editing_reference_id:
                # Get existing items
                existing_items = WarehouseShipment.objects.filter(reference_id=editing_reference_id)
                existing_item_ids = list(existing_items.values_list('id', flat=True))
                
                # If the reference ID has changed, update all existing items
                if tracking_info != editing_reference_id:
                    print(f"Reference ID changed from '{editing_reference_id}' to '{tracking_info}'")
                    # Check if the new reference ID already exists
                    if WarehouseShipment.objects.filter(reference_id=tracking_info).exists():
                        messages.error(request, f"Cannot change reference ID to '{tracking_info}' as it already exists.")
                        return redirect("warehouse_shipment")
                    
                    # Update the reference ID for all existing items
                    existing_items.update(reference_id=tracking_info)
                
                # Track which items we've processed to identify which to delete later
                processed_ids = []
                
                # Process each item
                for i in range(len(client_items)):
                    client_item = client_items[i]
                    qty_shipped = int(quantities[i]) if i < len(quantities) and quantities[i] else 0
                    item_id = item_ids[i] if i < len(item_ids) and item_ids[i] else None
                    
                    if item_id and item_id.isdigit():
                                            # This is an existing item, update it
                        item_id = int(item_id)
                        processed_ids.append(item_id)
                        try:
                            item = WarehouseShipment.objects.get(id=item_id)
                            # Store old quantity before updating
                            old_qty = item.ship_qty
                            
                            item.client_id = client_item
                            item.item = client_item
                            item.ship_date = ship_date
                            item.ship_qty = qty_shipped
                            item.reference_id = tracking_info
                            item.checked_by = user
                            item.expected_arrival_date = expected_arrival_date
                            item.save()
                            
                            # Since we've already added back the original quantity to inventory,
                            # we need to treat this as a new shipment to properly deduct the new quantity
                            update_inventory_when_shipping(client_item, qty_shipped, is_new=True)
                            print(f"Updated item ID {item_id}")
                        except WarehouseShipment.DoesNotExist:
                                                        # Item ID no longer exists, create a new one
                            new_item = WarehouseShipment.objects.create(
                                client_id=client_item,
                                item=client_item,
                                ship_date=ship_date,
                                ship_qty=qty_shipped,
                                reference_id=tracking_info,
                                checked_by=user,
                                expected_arrival_date=expected_arrival_date
                            )
                            # Update inventory quantity_available (new item)
                            update_inventory_when_shipping(client_item, qty_shipped, is_new=True)
                            processed_ids.append(new_item.id)
                            print(f"Created new item (ID {new_item.id}) as existing ID {item_id} not found")
                    else:
                        # This is a new item added during editing
                        new_item = WarehouseShipment.objects.create(
                            client_id=client_item,
                            item=client_item,
                            ship_date=ship_date,
                            ship_qty=qty_shipped,
                            reference_id=tracking_info,
                            checked_by=user,
                            expected_arrival_date=expected_arrival_date
                        )
                        # Update inventory quantity_available (new item)
                        update_inventory_when_shipping(client_item, qty_shipped, is_new=True)
                        processed_ids.append(new_item.id)
                        print(f"Added new item with ID {new_item.id}")
                
                # Delete items that were removed during editing
                for item_id in existing_item_ids:
                    if item_id not in processed_ids:
                        # Get item details before deleting to update inventory
                        try:
                            removed_item = WarehouseShipment.objects.get(id=item_id)
                            client_id = removed_item.client_id
                            ship_qty = removed_item.ship_qty
                            
                            # Delete the item
                            removed_item.delete()
                            
                            # No need to update inventory here since we've already added back 
                            # all quantities at the beginning of the edit operation
                            print(f"Deleted item with ID {item_id} for {client_id} (no inventory update needed)")
                            
                            print(f"Deleted item with ID {item_id}")
                        except WarehouseShipment.DoesNotExist:
                            print(f"Item with ID {item_id} already deleted")
                
                messages.success(request, f"Updated warehouse shipment with {len(client_items)} items successfully!")
            else:
                # Not editing - create new items
                for i in range(len(client_items)):
                    client_item = client_items[i]
                    qty_shipped = int(quantities[i]) if i < len(quantities) and quantities[i] else 0
                    
                    # Save the warehouse shipment entry
                    WarehouseShipment.objects.create(
                        client_id=client_item,
                        item=client_item,
                        ship_date=ship_date,
                        ship_qty=qty_shipped,
                        reference_id=tracking_info,
                        checked_by=user,
                        expected_arrival_date=expected_arrival_date
                    )
                    
                    # Update inventory quantity_available
                    update_inventory_when_shipping(client_item, qty_shipped, is_new=True)
                
                messages.success(request, f"New warehouse shipment with {len(client_items)} items submitted!")
            
            # After successful shipment creation/update, update inventory quantities
            update_inventory_shipped_quantities()
            
            # Clear any editing session data
            if request.session.get("editing_warehouse_shipment"):
                del request.session["editing_warehouse_shipment"]
                request.session.save()
            
            # Return redirect instead of JSON response to avoid displaying JSON to user
            return redirect('warehouse_shipment')

        except Exception as e:
            print("error ::", e)
            messages.error(request, f"Error submitting warehouse shipment: {str(e)}")

    # Get previous submissions (grouped by reference ID)
    previous_submissions = []
    
    # Use values to get unique reference IDs and annotate to count items
    container_data = WarehouseShipment.objects.values('reference_id').annotate(
        product_count=Count('id'),
        id=Min('id')  # Use the lowest ID as a reference
    ).order_by('-id')  # Sort by newest first
    
    # For each container, get additional details from a representative item
    for container in container_data:
        # Get the first item for this container
        reference_item = WarehouseShipment.objects.filter(reference_id=container['reference_id']).first()
        
        if reference_item:
            previous_submissions.append({
                'id': reference_item.id,
                'reference_id': reference_item.reference_id,
                'ship_date': reference_item.ship_date,
                'expected_arrival': reference_item.expected_arrival_date,
                'product_count': container['product_count'],
                'checked_by': reference_item.checked_by.name if reference_item.checked_by else "Unknown"
            })
    
    paginator = Paginator(previous_submissions, 10)  # Show 10 submissions per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'user_name': user_name,
        'previous_submissions': page_obj,
        'is_paginated': page_obj.has_other_pages(),
        'page_obj': page_obj
    }
    
    return render(request, 'warehouse_shipment.html', context)

@session_login_required
def check_warehouse_container_exists(request):
    """
    API to check if a warehouse reference ID exists.
    Uses case-insensitive matching for consistent results.
    """
    reference_id = request.GET.get('reference_id', '')
    
    if not reference_id:
        return JsonResponse({'exists': False, 'count': 0})
    
    # Count items with this reference ID (case-insensitive)
    count = WarehouseShipment.objects.filter(reference_id__iexact=reference_id).count()
    
    return JsonResponse({'exists': count > 0, 'count': count})

@session_login_required
def get_warehouse_container_data(request):
    """
    API to get warehouse container details.
    Uses case-insensitive matching for consistent results.
    """
    reference_id = request.GET.get('reference_id')
    if not reference_id:
        return JsonResponse({'success': False, 'message': 'Reference ID is required'})
    
    try:
        # Get all items with this reference ID (case-insensitive)
        items = WarehouseShipment.objects.filter(reference_id__iexact=reference_id)
        
        if not items.exists():
            return JsonResponse({'success': False, 'message': 'Container not found'})
        
        # Get the first item for reference dates
        reference_item = items.first()
        
        # Format dates for form fields
        ship_date = reference_item.ship_date.strftime('%Y-%m-%d') if reference_item.ship_date else ''
        expected_arrival = reference_item.expected_arrival_date.strftime('%Y-%m-%d') if reference_item.expected_arrival_date else ''
        
        # Format items for JSON response
        item_list = []
        for item in items:
            # Get product name from ProductData if available (case-insensitive)
            try:
                product = ProductData.objects.filter(client_id__iexact=item.client_id).first()
                product_name = product.description if product else None
                if not product_name:
                    raise ProductData.DoesNotExist
            except ProductData.DoesNotExist:
                product_name = item.item or "Unknown Product"
            
            item_list.append({
                'id': item.id,
                'client_id': item.client_id,
                'product_name': product_name,
                'quantity': item.ship_qty or 1
            })
        
        return JsonResponse({
            'success': True,
            'reference_id': reference_id,
            'ship_date': ship_date,
            'expected_arrival': expected_arrival,
            'items': item_list
        })
    
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})

@session_login_required
def get_warehouse_shipment_items(request):
    """
    API to get all items from a warehouse shipment by reference ID.
    Uses case-insensitive matching for consistent results.
    """
    reference_id = request.GET.get('reference_id', '')
    
    if not reference_id:
        return JsonResponse({'success': False, 'message': 'Reference ID is required'})
    
    try:
        # Find all items with the given reference ID (case-insensitive)
        items = WarehouseShipment.objects.filter(reference_id__iexact=reference_id)
        
        if not items.exists():
            return JsonResponse({'success': False, 'message': 'No items found with this reference ID'})
        
        # Format items for response
        item_list = []
        for item in items:
            # Get product name from ProductData if available (case-insensitive)
            try:
                product = ProductData.objects.filter(client_id__iexact=item.client_id).first()
                product_name = product.description if product else None
                if not product_name:
                    raise ProductData.DoesNotExist
            except ProductData.DoesNotExist:
                product_name = item.item or "Unknown Product"
            
            item_list.append({
                'id': item.id,
                'client_id': item.client_id,
                'product_name': product_name,
                'quantity': item.ship_qty,
                'ship_qty': item.ship_qty
            })
        
        return JsonResponse({
            'success': True,
            'reference_id': reference_id,
            'items': item_list
        })
    
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})

def update_inventory_shipped_quantities():
    """
    Calculate the sum of shipped quantities from warehouse_shipment table
    and update the shipped_to_hotel_quantity in inventory table.
    
    This function:
    1. Groups all warehouse shipments by client_id (case-insensitive)
    2. Calculates the total ship_qty for each client_id
    3. Updates the shipped_to_hotel_quantity in the inventory table for matching client_id
    """
    from django.db.models import Sum, F
    from django.db.models.functions import Lower
    from hotel_bot_app.models import Inventory, WarehouseShipment
    
    # Get all warehouse shipments grouped by client_id (converted to lowercase for case-insensitive grouping)
    # We use Lower() function to make the comparison case-insensitive
    shipment_sums = WarehouseShipment.objects.annotate(
        client_id_lower=Lower('client_id')
    ).values('client_id_lower').annotate(
        total_shipped=Sum('ship_qty'),
        original_client_id=F('client_id')  # Keep one original client_id for reference
    ).values('client_id_lower', 'total_shipped', 'original_client_id')
    
    update_count = 0
    # Update each inventory record with the calculated sum
    for shipment in shipment_sums:
        # Get client_id and total
        client_id_lower = shipment['client_id_lower']
        total_shipped = shipment['total_shipped']
        original_client_id = shipment['original_client_id']
        
        # Find and update all inventory records with this client_id (case-insensitive)
        updated = Inventory.objects.filter(
            client_id__iexact=original_client_id
        ).update(shipped_to_hotel_quantity=total_shipped)
        
        # If no records found with exact case, try with lowercase comparison
        if updated == 0:
            updated = Inventory.objects.filter(
                client_id__iexact=client_id_lower
            ).update(shipped_to_hotel_quantity=total_shipped)
        
        update_count += updated
        print(f"Client ID: {original_client_id} (lowercase: {client_id_lower}), Total: {total_shipped}, Updated: {updated} records")
        
    print(f"Updated shipped_to_hotel_quantity for {update_count} inventory items based on {len(shipment_sums)} unique client IDs")
    return update_count

def update_inventory_hotel_warehouse_quantities():
    """
    Calculate the sum of received and damaged quantities from hotel_warehouse table
    and update the received_at_hotel_quantity and damaged_quantity_at_hotel in inventory table.
    
    This function:
    1. Groups all hotel warehouse records by client_item (case-insensitive)
    2. Calculates the total quantity_received and damaged_qty for each client_item
    3. Updates the received_at_hotel_quantity and damaged_quantity_at_hotel in the inventory table
    """
    from django.db.models import Sum, F
    from django.db.models.functions import Lower
    from hotel_bot_app.models import Inventory, HotelWarehouse
    
    # Get all hotel warehouse records grouped by client_item (converted to lowercase for case-insensitive grouping)
    warehouse_sums = HotelWarehouse.objects.annotate(
        client_item_lower=Lower('client_item')
    ).values('client_item_lower').annotate(
        total_received=Sum('quantity_received'),
        total_damaged=Sum('damaged_qty'),
        original_client_item=F('client_item')  # Keep one original client_item for reference
    ).values('client_item_lower', 'total_received', 'total_damaged', 'original_client_item')
    
    update_count = 0
    # Update each inventory record with the calculated sums
    for warehouse in warehouse_sums:
        # Get client_item and totals
        client_item_lower = warehouse['client_item_lower']
        total_received = warehouse['total_received']
        total_damaged = warehouse['total_damaged']
        original_client_item = warehouse['original_client_item']
        
        # Find and update all inventory records with this client_item (case-insensitive)
        updated = Inventory.objects.filter(
            client_id__iexact=original_client_item
        ).update(
            received_at_hotel_quantity=total_received,
            damaged_quantity_at_hotel=total_damaged
        )
        
        # If no records found with exact case, try with lowercase comparison
        if updated == 0:
            updated = Inventory.objects.filter(
                client_id__iexact=client_item_lower
            ).update(
                received_at_hotel_quantity=total_received,
                damaged_quantity_at_hotel=total_damaged
            )
        
        update_count += updated
        print(f"Client Item: {original_client_item}, Received: {total_received}, Damaged: {total_damaged}, Updated: {updated} records")
        
    print(f"Updated hotel warehouse quantities for {update_count} inventory items based on {len(warehouse_sums)} unique client items")
    return update_count

def update_inventory_damaged_quantities():
    """
    Calculate the sum of damaged_qty from inventory_received table
    and update the damaged_quantity column in inventory table.
    
    This function:
    1. Groups all inventory received records by client_id (case-insensitive)
    2. Calculates the total damaged_qty for each client_id
    3. Updates the damaged_quantity in the inventory table for matching client_id
    """
    from django.db.models import Sum, F
    from django.db.models.functions import Lower
    from hotel_bot_app.models import Inventory, InventoryReceived
    
    # Get all inventory received records grouped by client_id (converted to lowercase for case-insensitive grouping)
    damaged_sums = InventoryReceived.objects.annotate(
        client_id_lower=Lower('client_id')
    ).values('client_id_lower').annotate(
        total_damaged=Sum('damaged_qty'),
        original_client_id=F('client_id')  # Keep one original client_id for reference
    ).values('client_id_lower', 'total_damaged', 'original_client_id')
    
    update_count = 0
    # Update each inventory record with the calculated sum
    for damaged in damaged_sums:
        # Get client_id and total
        client_id_lower = damaged['client_id_lower']
        total_damaged = damaged['total_damaged']
        original_client_id = damaged['original_client_id']
        
        # Find and update all inventory records with this client_id (case-insensitive)
        updated = Inventory.objects.filter(
            client_id__iexact=original_client_id
        ).update(damaged_quantity=total_damaged)
        
        # If no records found with exact case, try with lowercase comparison
        if updated == 0:
            updated = Inventory.objects.filter(
                client_id__iexact=client_id_lower
            ).update(damaged_quantity=total_damaged)
        
        update_count += updated
        print(f"Client ID: {original_client_id} (lowercase: {client_id_lower}), Total Damaged: {total_damaged}, Updated: {updated} records")
        
    print(f"Updated damaged_quantity for {update_count} inventory items based on {len(damaged_sums)} unique client IDs")
    return update_count

def update_inventory_received_quantities():
    """
    Calculate the sum of received_qty from inventory_received table
    and update the qty_received column in inventory table.
    
    This function:
    1. Groups all inventory received records by client_id (case-insensitive)
    2. Calculates the total received_qty for each client_id
    3. Updates the qty_received in the inventory table for matching client_id
    """
    from django.db.models import Sum, F
    from django.db.models.functions import Lower
    from hotel_bot_app.models import Inventory, InventoryReceived
    
    # Get all inventory received records grouped by client_id (converted to lowercase for case-insensitive grouping)
    received_sums = InventoryReceived.objects.annotate(
        client_id_lower=Lower('client_id')
    ).values('client_id_lower').annotate(
        total_received=Sum('received_qty'),
        original_client_id=F('client_id')  # Keep one original client_id for reference
    ).values('client_id_lower', 'total_received', 'original_client_id')
    
    update_count = 0
    # Update each inventory record with the calculated sum
    for received in received_sums:
        # Get client_id and total
        client_id_lower = received['client_id_lower']
        total_received = received['total_received']
        original_client_id = received['original_client_id']
        
        # Find and update all inventory records with this client_id (case-insensitive)
        updated = Inventory.objects.filter(
            client_id__iexact=original_client_id
        ).update(qty_received=total_received)
        
        # If no records found with exact case, try with lowercase comparison
        if updated == 0:
            updated = Inventory.objects.filter(
                client_id__iexact=client_id_lower
            ).update(qty_received=total_received)
        
        update_count += updated
        print(f"Client ID: {original_client_id} (lowercase: {client_id_lower}), Total Received: {total_received}, Updated: {updated} records")
        
    print(f"Updated qty_received for {update_count} inventory items based on {len(received_sums)} unique client IDs")
    return update_count

@session_login_required
def get_available_quantity(request):
    """
    API endpoint to get the available quantity for a client item from the inventory table.
    Uses case-insensitive matching to ensure consistent results regardless of case.
    When for_edit=true, temporarily adds the original quantity back to available.
    """
    client_item = request.GET.get('client_item', '')
    for_edit = request.GET.get('for_edit', 'false').lower() == 'true'
    edit_quantity = int(request.GET.get('edit_quantity', 0) or 0)
    
    if not client_item:
        return JsonResponse({'success': False, 'message': 'Client item is required'})
    
    try:
        # Use Lower database function to ensure case-insensitive matching
        from django.db.models.functions import Lower
        
        # Find the inventory item with case-insensitive matching
        # Use annotate to get case-insensitive matching
        inventory_items = Inventory.objects.annotate(
            client_id_lower=Lower('client_id')
        ).filter(
            client_id_lower=client_item.lower()
        )
        
        # Check if we have multiple items with same client_id but different case
        if inventory_items.count() > 1:
            # Find the primary item (first one) and consolidate quantities to it
            primary_item = inventory_items.first()
            
            # Set other items to 0 quantity
            for item in inventory_items.exclude(id=primary_item.id):
                if item.quantity_available > 0:
                    # Transfer any available quantity to primary item
                    primary_item.quantity_available += (item.quantity_available or 0)
                    item.quantity_available = 0
                    item.save()
                    print(f"Consolidated quantity from {item.client_id} to {primary_item.client_id}")
            
            # Save primary item with consolidated quantity
            primary_item.save()
            
            # Continue with just the primary item
            inventory_item = primary_item
            quantity_available = inventory_item.quantity_available or 0
        elif inventory_items.exists():
            # Just one item found, proceed normally
            inventory_item = inventory_items.first()
            # Get the base quantity available
            quantity_available = inventory_item.quantity_available or 0
            
            # We no longer need to add back quantities here since we're
            # now doing that upfront when editing begins
            if for_edit:
                print(f"Edit mode for {client_item}, available quantity: {quantity_available}")
                # Don't modify the quantity here anymore
            
            return JsonResponse({
                'success': True, 
                'quantity_available': quantity_available,
                'client_id': inventory_item.client_id,
                'is_edit': for_edit,
                'original_quantity': edit_quantity
            })
        else:
            # Try a fallback approach with iexact if the above fails
            inventory_item = Inventory.objects.filter(client_id__iexact=client_item).first()
            if inventory_item:
                quantity_available = inventory_item.quantity_available or 0
                
                # We no longer need to add back quantities here
                if for_edit:
                    print(f"Edit mode for {client_item}, available quantity: {quantity_available} (fallback)")
                    # Don't modify the quantity here anymore
                
                return JsonResponse({
                    'success': True, 
                    'quantity_available': quantity_available,
                    'client_id': inventory_item.client_id,
                    'is_edit': for_edit,
                    'original_quantity': edit_quantity
                })
            return JsonResponse({'success': False, 'message': 'Item not found in inventory'})
    
    except Exception as e:
        print(f"Error in get_available_quantity: {str(e)}")
        return JsonResponse({'success': False, 'message': str(e)})

def update_inventory_when_shipping(client_id, ship_qty, is_new=True, old_qty=0):
    """
    Update the quantity_available in the inventory table when items are shipped to the hotel.
    
    Args:
        client_id (str): The client ID of the item being shipped
        ship_qty (int): The quantity being shipped
        is_new (bool): Whether this is a new shipment (True) or an edit (False)
        old_qty (int): If editing, the previous shipped quantity (not used anymore)
    
    Returns:
        bool: True if inventory was updated successfully, False otherwise
    """
    try:
        # Find the inventory record with case-insensitive matching
        inventory_item = Inventory.objects.filter(client_id__iexact=client_id).first()
        
        if inventory_item:
            # Simply subtract the shipped quantity
            if inventory_item.quantity_available is not None:
                # First make sure we don't go negative
                if inventory_item.quantity_available >= ship_qty:
                    inventory_item.quantity_available -= ship_qty
                    inventory_item.save()
                    print(f"Updated quantity_available for {client_id}: {inventory_item.quantity_available}")
                else:
                    print(f"Warning: Not enough quantity available for {client_id}. Available: {inventory_item.quantity_available}, Requested: {ship_qty}")
                    # Just set to 0 to avoid negative values
                    inventory_item.quantity_available = 0
                    inventory_item.save()
            
            return True
        else:
            print(f"Warning: No inventory record found for client ID {client_id}")
            return False
    
    except Exception as e:
        print(f"Error updating inventory quantity_available for {client_id}: {str(e)}")
        return False

def update_inventory_when_receiving(client_id, received_qty, damaged_qty, is_new=True, old_received=0, old_damaged=0):
    """
    Update the quantity_available in the inventory table when items are received.
    
    Args:
        client_id (str): The client ID of the item being received
        received_qty (int): The quantity being received (already excludes damaged items)
        damaged_qty (int): The damaged quantity being received (not used in calculation)
        is_new (bool): Whether this is a new receipt (True) or an edit (False)
        old_received (int): If editing, the previous received quantity
        old_damaged (int): If editing, the previous damaged quantity
    
    Returns:
        bool: True if inventory was updated successfully, False otherwise
    """
    try:
        # Find the inventory record with case-insensitive matching
        inventory_item = Inventory.objects.filter(client_id__iexact=client_id).first()
        
        if inventory_item:
            if is_new:
                # For new receipts, simply add the received quantity (already good qty)
                if inventory_item.quantity_available is not None:
                    inventory_item.quantity_available += received_qty
                    inventory_item.save()
                    print(f"Updated quantity_available for {client_id}: {inventory_item.quantity_available} (+{received_qty})")
            else:
                # For edits, just calculate the difference in received quantity
                received_diff = received_qty - old_received
                
                if inventory_item.quantity_available is not None:
                    inventory_item.quantity_available += received_diff
                    inventory_item.save()
                    print(f"Updated quantity_available for {client_id} (edit): {inventory_item.quantity_available}, diff: {received_diff}")
            
            return True
        else:
            print(f"Warning: No inventory record found for client ID {client_id}")
            return False
    
    except Exception as e:
        print(f"Error updating inventory quantity_available for {client_id}: {str(e)}")
        return False

@session_login_required
def restore_warehouse_inventory(request):
    """
    API to restore inventory quantities when editing a warehouse shipment.
    This adds back the shipped quantities to inventory to allow proper editing.
    """
    reference_id = request.GET.get('reference_id')
    if not reference_id:
        return JsonResponse({'success': False, 'message': 'Reference ID is required'})
    
    try:
        # Save the current edit in the session to handle page reloads
        request.session["editing_warehouse_shipment"] = reference_id
        request.session.save()
        
        # Get all items with this reference ID (case-insensitive)
        items = WarehouseShipment.objects.filter(reference_id__iexact=reference_id)
        
        if not items.exists():
            return JsonResponse({'success': False, 'message': 'Shipment not found'})
        
        updated_items = []
        
        # Add quantities back to inventory
        for item in items:
            client_id = item.client_id
            ship_qty = item.ship_qty
            
            # Find inventory item
            inventory_item = Inventory.objects.filter(client_id__iexact=client_id).first()
            if inventory_item and inventory_item.quantity_available is not None:
                # Add back the quantity
                inventory_item.quantity_available += ship_qty
                inventory_item.save()
                
                updated_items.append({
                    'client_id': client_id,
                    'added_quantity': ship_qty,
                    'new_available': inventory_item.quantity_available
                })
                
                print(f"Restored {ship_qty} to inventory for {client_id}, new available: {inventory_item.quantity_available}")
        
        return JsonResponse({
            'success': True,
            'reference_id': reference_id,
            'updated': updated_items
        })
    
    except Exception as e:
        print(f"Error restoring inventory: {str(e)}")
        return JsonResponse({'success': False, 'message': str(e)})
    
@session_login_required
def revert_warehouse_inventory(request):
    """
    API to revert the inventory quantities after canceling editing a warehouse shipment.
    This subtracts the shipped quantities from inventory to restore the original state.
    """
    reference_id = request.GET.get('reference_id')
    if not reference_id:
        return JsonResponse({'success': False, 'message': 'Reference ID is required'})
    
    try:
        # Clear the edit from session
        if request.session.get("editing_warehouse_shipment") == reference_id:
            del request.session["editing_warehouse_shipment"]
            request.session.save()
        
        # Get all items with this reference ID (case-insensitive)
        items = WarehouseShipment.objects.filter(reference_id__iexact=reference_id)
        
        if not items.exists():
            return JsonResponse({'success': False, 'message': 'Shipment not found'})
        
        updated_items = []
        
        # Subtract quantities from inventory to restore original state
        for item in items:
            client_id = item.client_id
            ship_qty = item.ship_qty
            
            # Find inventory item
            inventory_item = Inventory.objects.filter(client_id__iexact=client_id).first()
            if inventory_item and inventory_item.quantity_available is not None:
                # Subtract back the quantity, ensuring we don't go negative
                if inventory_item.quantity_available >= ship_qty:
                    inventory_item.quantity_available -= ship_qty
                else:
                    # If not enough available, just set to 0
                    inventory_item.quantity_available = 0
                
                inventory_item.save()
                
                updated_items.append({
                    'client_id': client_id,
                    'subtracted_quantity': ship_qty,
                    'new_available': inventory_item.quantity_available
                })
                
                print(f"Subtracted {ship_qty} from inventory for {client_id}, new available: {inventory_item.quantity_available}")
        
        return JsonResponse({
            'success': True,
            'reference_id': reference_id,
            'updated': updated_items
        })
    
    except Exception as e:
        print(f"Error reverting inventory: {str(e)}")
        return JsonResponse({'success': False, 'message': str(e)})
    
@login_required
def create_django_admin_user(request):
    """Create a Django admin user with is_administrator=True but is_superuser=False
    
    This creates users who can access most admin features but can't manage users.
    """
    # Only superusers can create Django admin users
    if not request.user.is_superuser:
        return HttpResponseForbidden("Only superusers can create Django admin users")
    if request.method == "POST":
        username = request.POST.get("username")
        email = request.POST.get("email")
        password = request.POST.get("password")
        
        if not username or not email or not password:
            messages.error(request, "All fields are required")
            return render(request, "create_django_admin.html")
            
        # Check if user already exists
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists")
            return render(request, "create_django_admin.html")
            
        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already exists")
            return render(request, "create_django_admin.html")
            
        # Create Django user
        django_user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=True,
            is_superuser=False  # Not a superuser, just an administrator
        )
        
        # Set administrator flag
        django_user.profile.is_administrator = True
        django_user.profile.save()
        
        # Create matching InvitedUser if needed
        if not InvitedUser.objects.filter(email=email).exists():
            InvitedUser.objects.create(
                name=username,
                email=email,
                role=['administrator'],
                is_administrator=True,
                status='activated',
                password=bcrypt.hashpw(password.encode(), bcrypt.gensalt())
            )
        
        messages.success(request, f"Administrator user '{username}' created successfully (with is_administrator=True but NOT superuser)")
        return redirect("user_management")
        
    return render(request, "create_django_admin.html")
    
# Add this helper function near the other inventory update helpers
def recalculate_quantity_available(client_id):
    from django.db.models import Sum
    from hotel_bot_app.models import InventoryReceived, Inventory
    # Use iexact to get case-insensitive matches for received quantities
    total_received = InventoryReceived.objects.filter(client_id__iexact=client_id).aggregate(Sum('received_qty'))['received_qty__sum'] or 0
    
    # Find all inventory items that match this client_id (case-insensitive)
    inventory_items = Inventory.objects.filter(client_id__iexact=client_id)
    
    if inventory_items.exists():
        # If there are multiple inventory items with different case variations
        if inventory_items.count() > 1:
            # Keep the first one and update it with the total received quantity
            primary_item = inventory_items.first()
            primary_item.quantity_available = total_received
            primary_item.save()
            
            # For all other items (different case variations), set quantity to 0 to avoid confusion
            for item in inventory_items.exclude(id=primary_item.id):
                item.quantity_available = 0
                item.save()
                print(f"Set quantity to 0 for duplicate inventory item: {item.client_id}")
        else:
            # Just one item found, update it normally
            inventory_item = inventory_items.first()
            inventory_item.quantity_available = total_received
            inventory_item.save()

# Add this helper function for hotel warehouse quantities
def recalculate_hotel_warehouse_quantity(client_id):
    from django.db.models import Sum
    from hotel_bot_app.models import HotelWarehouse, Inventory
    # Use iexact to get case-insensitive matches for hotel warehouse quantities
    total_received = HotelWarehouse.objects.filter(client_item__iexact=client_id).aggregate(Sum('quantity_received'))['quantity_received__sum'] or 0
    total_damaged = HotelWarehouse.objects.filter(client_item__iexact=client_id).aggregate(Sum('damaged_qty'))['damaged_qty__sum'] or 0
    
    print(f"Recalculating for {client_id}: received={total_received}, damaged={total_damaged}")
    
    # Find all inventory items that match this client_id (case-insensitive)
    inventory_items = Inventory.objects.filter(client_id__iexact=client_id)
    
    if inventory_items.exists():
        # If there are multiple inventory items with different case variations
        if inventory_items.count() > 1:
            # Keep the first one and update it with the total received quantity
            primary_item = inventory_items.first()
            primary_item.hotel_warehouse_quantity = total_received
            primary_item.received_at_hotel_quantity = total_received  # Update received_at_hotel_quantity too
            primary_item.damaged_quantity_at_hotel = total_damaged    # Update damaged_quantity_at_hotel too
            primary_item.save()
            
            # For all other items (different case variations), set quantity to 0 to avoid confusion
            for item in inventory_items.exclude(id=primary_item.id):
                item.hotel_warehouse_quantity = 0
                item.received_at_hotel_quantity = 0     # Set to 0 for duplicate items
                item.damaged_quantity_at_hotel = 0      # Set to 0 for duplicate items
                item.save()
                print(f"Set hotel quantities to 0 for duplicate inventory item: {item.client_id}")
        else:
            # Just one item found, update it normally
            inventory_item = inventory_items.first()
            inventory_item.hotel_warehouse_quantity = total_received
            inventory_item.received_at_hotel_quantity = total_received  # Update received_at_hotel_quantity too
            inventory_item.damaged_quantity_at_hotel = total_damaged    # Update damaged_quantity_at_hotel too
            inventory_item.save()
            print(f"Updated hotel quantities for {client_id}: received={total_received}, damaged={total_damaged}")

# ... inside warehouse_receiver view, after each new HotelWarehouse.objects.create(...):
    HotelWarehouse.objects.create(
        reference_id=reference_id,
        client_item=client_item,
        quantity_received=received_qty,
        damaged_qty=damaged_qty,  # Add the damaged quantity
        checked_by=user,
        received_date=received_date
    )





def chat_interface(request):
    """Serve the chat interface"""
    return render(request, 'chat.html')

@csrf_exempt
@require_http_methods(["POST"])
def chat_stream(request):
    """Streaming chat endpoint"""
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        session_id = data.get('session_id')
        user_name = data.get('user_name', 'Anonymous')
        
        if not question:
            return JsonResponse({
                'error': 'Question is required'
            }, status=400)
        
        # Create chatbot instance
        chatbot = InventoryChatbot()
        
        # Create streaming response
        def generate_response():
            try:
                # Process the question with streaming
                for event in chatbot.process_question_streaming(question, session_id):
                    if event["type"] == "thinking":
                        yield f"data: {json.dumps({'content': event['content'], 'type': 'thinking'})}\n\n"
                    elif event["type"] == "thinking_end":
                        yield f"data: {json.dumps({'content': event['content'], 'type': 'thinking_end'})}\n\n"
                    elif event["type"] == "final":
                        yield f"data: {json.dumps({'content': event['content'], 'type': 'final'})}\n\n"
                    elif event["type"] == "end":
                        yield f"data: {json.dumps({'type': 'end'})}\n\n"
                    elif event["type"] == "error":
                        yield f"data: {json.dumps({'error': event['content'], 'type': 'error'})}\n\n"
                
            except Exception as e:
                logger.error(f"Error in streaming response: {e}")
                yield f"data: {json.dumps({'error': str(e), 'type': 'error'})}\n\n"
        
        response = StreamingHttpResponse(
            generate_response(),
            content_type='text/plain'
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response
        
    except json.JSONDecodeError:
        return JsonResponse({
            'error': 'Invalid JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Error in chat_stream: {e}")
        return JsonResponse({
            'error': str(e)
        }, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def chat_sync(request):
    """Synchronous chat endpoint (non-streaming)"""
    try:
        data = json.loads(request.body)
        question = data.get('question', '').strip()
        session_id = data.get('session_id')
        user_name = data.get('user_name', 'Anonymous')
        
        if not question:
            return JsonResponse({
                'error': 'Question is required'
            }, status=400)
        
        # Create chatbot instance
        chatbot = InventoryChatbot()
        
        # Collect full response
        full_response = ""
        for chunk in chatbot.process_question(question, session_id):
            full_response += chunk
        
        return JsonResponse({
            'response': full_response,
            'session_id': session_id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'error': 'Invalid JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Error in chat_sync: {e}")
        return JsonResponse({
            'error': str(e)
        }, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def create_session(request):
    """Create a new chat session"""
    try:
        data = json.loads(request.body)
        user_name = data.get('user_name', 'Anonymous')
        
        chatbot = InventoryChatbot()
        session = chatbot.create_session(user_name)
        
        return JsonResponse({
            'session_id': session.id,
            'user_id': session.user.id,
            'started_at': session.started_at.isoformat(),
            'topic': session.topic
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'error': 'Invalid JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return JsonResponse({
            'error': str(e)
        }, status=500)

@require_http_methods(["GET"])
def get_chat_history(request, session_id):
    """Get chat history for a session"""
    try:
        session_id = int(session_id)
        limit = int(request.GET.get('limit', 10))
        
        chatbot = InventoryChatbot()
        history = chatbot.get_chat_history(session_id, limit)
        
        return JsonResponse({
            'session_id': session_id,
            'history': history
        })
        
    except ValueError:
        return JsonResponse({
            'error': 'Invalid session ID'
        }, status=400)
    except Exception as e:
        logger.error(f"Error getting chat history: {e}")
        return JsonResponse({
            'error': str(e)
        }, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def end_session(request, session_id):
    """End a chat session"""
    try:
        session_id = int(session_id)
        
        session = ChatSession.objects.get(id=session_id)
        session.ended_at = datetime.now()
        session.save()
        
        return JsonResponse({
            'session_id': session_id,
            'ended_at': session.ended_at.isoformat()
        })
        
    except ChatSession.DoesNotExist:
        return JsonResponse({
            'error': 'Session not found'
        }, status=404)
    except ValueError:
        return JsonResponse({
            'error': 'Invalid session ID'
        }, status=400)
    except Exception as e:
        logger.error(f"Error ending session: {e}")
        return JsonResponse({
            'error': str(e)
        }, status=500)

@csrf_exempt
@require_http_methods(["POST"])
def evaluate_response(request):
    """Evaluate a chatbot response"""
    try:
        data = json.loads(request.body)
        test_case = data.get('test_case', '').strip()
        expected_output = data.get('expected_output', '').strip()
        actual_output = data.get('actual_output', '').strip()
        
        if not all([test_case, expected_output, actual_output]):
            return JsonResponse({
                'error': 'test_case, expected_output, and actual_output are required'
            }, status=400)
        
        chatbot = InventoryChatbot()
        result = chatbot.evaluate_response(test_case, expected_output, actual_output)
        
        return JsonResponse(result)
        
    except json.JSONDecodeError:
        return JsonResponse({
            'error': 'Invalid JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Error evaluating response: {e}")
        return JsonResponse({
            'error': str(e)
        }, status=500)

@csrf_exempt
@require_http_methods(["GET", "POST"])
def manage_prompts(request):
    """Manage system prompts"""
    if request.method == "GET":
        try:
            prompts = ChatPrompt.objects.filter(is_active=True)
            prompt_list = []
            for prompt in prompts:
                prompt_list.append({
                    'id': prompt.id,
                    'name': prompt.name,
                    'instruction': prompt.instruction,
                    'examples': prompt.examples,
                    'validations': prompt.validations,
                    'created_at': prompt.created_at.isoformat(),
                    'is_active': prompt.is_active
                })
            
            return JsonResponse({
                'prompts': prompt_list
            })
            
        except Exception as e:
            logger.error(f"Error getting prompts: {e}")
            return JsonResponse({
                'error': str(e)
            }, status=500)
    
    elif request.method == "POST":
        try:
            data = json.loads(request.body)
            name = data.get('name', '').strip()
            instruction = data.get('instruction', '').strip()
            examples = data.get('examples', [])
            validations = data.get('validations', '').strip()
            
            if not all([name, instruction]):
                return JsonResponse({
                    'error': 'name and instruction are required'
                }, status=400)
            
            # Create or update prompt
            prompt, created = ChatPrompt.objects.get_or_create(
                name=name,
                defaults={
                    'instruction': instruction,
                    'examples': examples,
                    'validations': validations,
                    'is_active': True
                }
            )
            
            if not created:
                prompt.instruction = instruction
                prompt.examples = examples
                prompt.validations = validations
                prompt.save()
            
            return JsonResponse({
                'id': prompt.id,
                'name': prompt.name,
                'created': created
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'error': 'Invalid JSON'
            }, status=400)
        except Exception as e:
            logger.error(f"Error managing prompts: {e}")
            return JsonResponse({
                'error': str(e)
            }, status=500)

@require_http_methods(["GET"])
def get_evaluations(request):
    """Get evaluation results"""
    try:
        limit = int(request.GET.get('limit', 50))
        passed_only = request.GET.get('passed_only', 'false').lower() == 'true'
        
        evaluations = ChatEvaluation.objects.all()
        if passed_only:
            evaluations = evaluations.filter(passed=True)
        
        evaluations = evaluations.order_by('-timestamp')[:limit]
        
        eval_list = []
        for eval in evaluations:
            eval_list.append({
                'id': eval.id,
                'test_case': eval.test_case,
                'expected_output': eval.expected_output,
                'actual_output': eval.actual_output,
                'passed': eval.passed,
                'score': eval.score,
                'latency': eval.latency,
                'timestamp': eval.timestamp.isoformat()
            })
        
        return JsonResponse({
            'evaluations': eval_list,
            'total': len(eval_list)
        })
        
    except ValueError:
        return JsonResponse({
            'error': 'Invalid parameters'
        }, status=400)
    except Exception as e:
        logger.error(f"Error getting evaluations: {e}")
        return JsonResponse({
            'error': str(e)
        }, status=500)

@require_http_methods(["GET"])
def health_check(request):
    """Health check endpoint"""
    try:
        # Test database connection
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        
        # Test OpenAI connection (if API key is set)
        import os
        api_key = os.getenv("OPENAI_API_KEY")
        
        return JsonResponse({
            'status': 'healthy',
            'database': 'connected',
            'openai_configured': bool(api_key),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JsonResponse({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }, status=500)

# Legacy views for backward compatibility
def home_view(request):
    return HttpResponse("Welcome to the Inventory Management Chatbot!")

def chat_template_view(request):
    return HttpResponse("Chatbot API is working. Use /api/chat/ for streaming responses.") 