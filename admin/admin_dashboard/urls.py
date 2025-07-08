from django.urls import path
from . import views

app_name = 'admin_dashboard'

urlpatterns = [
    path('',views.show_login,name='login_page'),
    path('admin/login/', views.my_view, name='login'),
    path('logout',views.logout_view,name='logout'),
    path('admin_issues/', views.admin_issue_list, name='admin_issue_list'),
    path('admin_issues/edit/<int:issue_id>/', views.admin_issue_edit, name='admin_issue_edit'),
    path('admin_issues/<int:issue_id>/detail/', views.admin_issue_detail, name='admin_issue_detail'),
    path('admin_issues/<int:issue_id>/comment/admin/', views.admin_comment_create, name='admin_comment_create'),
    path('admin_issues/create/', views.admin_issue_create, name='admin_issue_create'),
    path('dashboard', views.dashboard, name='dashboard'),
    path('hotel-admin-issues/', views.hotel_admin_issue_dashboard, name='hotel_admin_issue_dashboard'),
]
