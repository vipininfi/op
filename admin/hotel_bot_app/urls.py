from django.urls import path
from .views import *
from .views import chat_stream
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('api/chat/stream/', chat_stream, name='chat_stream_api'),
    path('api/session/create/', create_session_api, name='create_session_api'),
    path('chat/', chatbot, name='chatbot'),
    path('home/', home, name='home'),
    path('api/chatbot/', chatbot_api, name='chatbot_api'),  # API for chatbot
    path('display_prompts/', display_prompts, name='edit_prompts'),  # Changed name to edit_prompts
    path('update_prompt/', update_prompt, name='update_prompt'),  # API for chatbot,
    path("api/get_chat_history/", get_chat_history, name="get_chat_history"),   
    path("user_management/", user_management, name="user_management"),  
    path("add_users_roles/", add_users_roles, name="add_users_roles"), 
    path('users/roles/edit/<int:user_id>/', edit_users_roles, name='edit_users_roles'),
    path("user_login/", user_login, name="user_login"), 
    path("create_django_admin/", create_django_admin_user, name="create_django_admin"),
    path('rooms/', room_data_list, name='room_data_list'),
    path('rooms/add/', add_room, name='add_room'),  # Add room view
    path('get_room_models/', get_room_models, name='get_room_models'),
    path('rooms/edit/', edit_room, name='edit_room'),
    path('delete-room/', delete_room, name='delete_room'),
    path('room-models/', room_model_list, name='room_model_list'),
    path('inventory/', inventory_list, name='inventory_list'),
    path('room-models/save/', save_room_model, name='save_room_model'),
    path('room-models/delete/', delete_room_model, name='delete_room_model'),
    path('installation-form/', installation_form, name='installation_form'),
    path('get-room-type/', get_room_type, name='get_room_type'),
    path('user_logout',user_logout,name='user_logout'),
    path('save_inventory/',save_inventory,name='save_inventory'),
    # path('save_admin_installation/',save_admin_installation,name='save_admin_installation'),
    path('delete_inventory/',delete_inventory,name='delete_inventory'),
    path('delete_products_data/',delete_products_data,name='delete_products_data'),
    path('delete_installation/',delete_installation,name='delete_installation'),
    path('install/',install_list,name='install_list'),
    path('products/',product_data_list,name='product_data_list'),
    path('save_product_data/',save_product_data,name='save_product_data'),
    path('schedule/',schedule_list,name='schedule_list'),
    path('save_schedule/',save_schedule,name='save_schedule'),
    path('delete_schedule/',delete_schedule,name='delete_schedule'),
    path('inventory_shipment/', inventory_shipment, name='inventory_shipment'),
    path('get_product_item_num/', get_product_item_num, name='get_product_item_num'),
    path('inventory_received/', inventory_received, name='inventory_received'),
    path('inventory_received_item_num/', inventory_received_item_num, name='inventory_received_item_num'),
    path('get_received_item_details/', get_received_item_details, name='get_received_item_details'),
    path('inventory_pull/', inventory_pull, name='inventory_pull'),
    path('chat_history/', chat_history, name='chat_history'),
    path('chat_history/<int:session_id>/', view_chat_history, name='view_chat_history'),
    path('get_shipment_details/', get_shipment_details, name='get_shipment_details'),
    path('check_container_exists/', check_container_exists, name='check_container_exists'),
    path('get_container_data/', get_container_data, name='get_container_data'),
    
    # Warehouse Shipment URLs
    path('warehouse_shipment/', warehouse_shipment, name='warehouse_shipment'),
    path('check_warehouse_container_exists/', check_warehouse_container_exists, name='check_warehouse_container_exists'),
    path('get_warehouse_container_data/', get_warehouse_container_data, name='get_warehouse_container_data'),
    
    # Warehouse Shipment Items API
    path('get_warehouse_shipment_items/', get_warehouse_shipment_items, name='get_warehouse_shipment_items'),
    path('get_available_quantity/', get_available_quantity, name='get_available_quantity'),
    
    # New URLs for Product Room Model
    path('product-room-models/', product_room_model_list, name='product_room_model_list'),
    path('save_product_room_model/', save_product_room_model, name='save_product_room_model'),
    path('delete_product_room_model/', delete_product_room_model, name='delete_product_room_model'),
    path('get_floor_products/', get_floor_products, name='get_floor_products'),
    path('get_room_products/', get_room_products, name='get_room_products'),
    # New URLs for user-facing product lists with download 
    path('floor-products/', floor_products_list, name='floor_products_list'),
    path('room-products/', room_number_products_list, name='room_number_products_list'),

    # URLs for admin installation management
    path('admin/installation/details/', admin_get_installation_details, name='admin_get_installation_details'),
    path('admin/installation/save/', admin_save_installation_details, name='admin_save_installation_details'),

    path('issue_list/', issue_list, name='issue_list'),
    path('issue_detail/<int:issue_id>/', issue_detail, name='issue_detail'),
    path('issue_create/', issue_create, name='issue_create'),
    path('issues/<int:issue_id>/comment/invited/', invited_user_comment_create, name='invited_user_comment_create'),
    path('get_container_received_items/', get_container_received_items, name='get_container_received_items'),
    path('hotel_warehouse/', hotel_warehouse, name='hotel_warehouse'),
    path('warehouse_receiver/', warehouse_receiver, name='warehouse_receiver'),
    path('get_warehouse_receipt_details/', get_warehouse_receipt_details, name='get_warehouse_receipt_details'),
    path('get_warehouse_receipts/', get_warehouse_receipts, name='get_warehouse_receipts'),
    
    # API endpoint for warehouse request items
    path('api/warehouse_request_items/', warehouse_request_items, name='warehouse_request_items'),
    path('get_previous_warehouse_requests/', get_previous_warehouse_requests, name='get_previous_warehouse_requests'),
    
    # Inventory restoration/reversion APIs for warehouse shipments
    path('restore_warehouse_inventory/', restore_warehouse_inventory, name='restore_warehouse_inventory'),
    path('revert_warehouse_inventory/', revert_warehouse_inventory, name='revert_warehouse_inventory'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
