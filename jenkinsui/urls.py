from django.urls import path
from .views import home, edit_jenkins, view_jenkins, create_jenkins, delete_jenkins, restart_jenkins

urlpatterns = [
    path("", home, name="home"),
    path("create/", create_jenkins, name="create"),
    path("delete/<str:name>/", delete_jenkins, name="delete"),
    path("restart/<str:name>/", restart_jenkins, name="restart"),
    path("view/<str:name>/", view_jenkins, name="view"),
    path("edit/<str:name>/", edit_jenkins, name="edit"),

]
