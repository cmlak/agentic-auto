from django.shortcuts import render
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, render, redirect
from django.http import Http404
from django.urls import reverse
from django.views import generic, View
from collections import defaultdict
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
import logging
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.decorators.cache import never_cache
from django.views.generic import (ListView, DetailView, UpdateView, DeleteView)
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone

from register.models import Profile
from register.forms import ProfileCreateForm

# Get an instance of a logger
logger = logging.getLogger(__name__)

# Create your views here.
@login_required(login_url="register:login")
def IndexView(request):

    return render(request, "main.html")

# Create authentication related views
def logout_request(request):
    # Get the user object based on session id in request
    print("Log out the user `{}`".format(request.user.username))
    # Logout user in the request
    logout(request)
    # Redirect user back to course list view
    # return redirect('onlinecourse:popular_course_list')
    # return redirect('main')
    return redirect('register:login')

@never_cache
def login_request(request):
    context = {}
    # Handles POST request
    if request.method == "POST":
        # Get username and password from request.POST dictionary
        username = request.POST['username']
        password = request.POST['psw']
        # Try to check if provide credential can be authenticated
        user = authenticate(username=username, password=password)
        if user is not None:
            # If user is valid, call login method to login current user
            login(request, user)
            # return redirect('onlinecourse:popular_course_list')
            return redirect("register:main")
        else:
            # If not, return to login page again
            return render(request, 'register/user_login.html', context)

    else:
        return render(request, 'register/user_login.html', context)

@never_cache
def registration_request(request):
    context = {}
    # If it is a GET request, just render the registration page
    if request.method == 'GET':
        return render(request, 'register/user_registration.html', context)
        # return render(request, 'todolist:upload_list_select.html', context)
    # If it is a POST request
    elif request.method == 'POST':
        # Get user information from request.POST
        username = request.POST['username']
        password = request.POST['psw']
        first_name = request.POST['firstname']
        last_name = request.POST['lastname']
        email = request.POST['email']
        
        department = request.POST['department']  # Get department from the for
        
        user_exist = False
        try:
            # Check if user already exists
            User.objects.get(username=username)
            user_exist = True
        except:
            # If not, simply log this is a new user
            logger.debug("{} is new user".format(username))
        # If it is a new user
        if not user_exist:
            # Create user in auth_user table
            user = User.objects.create_user(username=username, 
                                            first_name=first_name, 
                                            last_name=last_name,
                                            password=password,
                                            email=email)
            
            # Create a Profile instance and link it to the User
            Profile.objects.create(user=user, department=department) 
                        
            # Login the user and redirect to course list page
            login(request, user)
            
            return redirect("main")
        else:
            return render(request, 'register/user_registration.html', context)
        
@login_required
def registration_update(request):
    user = request.user
    profile = get_object_or_404(Profile, user=user)  # Retrieve the user's profile

    if request.method == 'POST':
        user.first_name = request.POST.get('firstname', user.first_name)
        user.last_name = request.POST.get('lastname', user.last_name)
        new_password = request.POST.get('psw')
        user.email = request.POST.get('email', user.email)
        profile.department = request.POST.get('department', profile.department)

        if new_password:
            user.set_password(new_password)
            user.save()
            update_session_auth_hash(request, user)  # Important! Update session

        user.save()
        profile.save()

        # Record update time
        user.date_joined = timezone.now() # or user.last_login = timezone.now() depending on how you want to track.
        user.save()
        
        messages.success(request, "User's profile updated successfully!")

        return redirect('register:profile_detail', pk=profile.pk)  # Redirect to profile detail page
    
    context = {
        'user': user,
        'profile': profile,
    }
    return render(request, 'register/user_update.html', context)
        
## User Profile

class ProfileListView(LoginRequiredMixin, ListView):
    login_url = "register:login"
    model = Profile
    template_name = 'profile_list.html'
    context_object_name = 'profile'

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.order_by('id')  # Order by ascending ID
    
    
@login_required(login_url="register:login")
def ProfileCreateView(request):
    if request.method == 'POST':
        form = ProfileCreateForm(request.POST.dict(), request.FILES)
        if form.is_valid():
            profile = form.save(commit=False)  # Don't save yet
            profile.user = request.user  # Associate with logged-in user
            profile.department = form.cleaned_data['department']
            profile.email = form.cleaned_data['email']
            profile.save()
            messages.success(request, 'Profile uploaded successfully!')  # Use Django's messages framework

            # Redirect to avoid resubmitting the form on refresh
            return redirect('register:profile_list')  # Replace with the actual name of your upload list view
        else:
            context = {'form': form, 'form_errors': form.errors}  # Pass errors to context
            return render(request, 'profile_add.html', context)


    else:
        form = ProfileCreateForm()

    context = {'form': ProfileCreateForm()}

    return render(request, 'profile_add.html', context)


class ProfileDetailView(LoginRequiredMixin, DetailView):
    login_url = "register:login"
    model = Profile
    template_name = 'profile_detail.html'
    context_object_name = 'profile'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.get_object()
        user = self.request.user

        # Permission check (including administrators in 'is_owner')
        context['is_owner'] = profile.user == user or user.is_staff

        return context

class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = Profile
    form_class = ProfileCreateForm
    template_name = 'user_update.html'  # Replace with your template name
    login_url = "register:login"  # Replace with your actual login URL

    def get_success_url(self):
        return reverse('register:profile_detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Optionally add a success message to the context
        if self.request.method == 'POST' and self.form.is_valid():
            context['success_message'] = 'Profile updated successfully!'

        return context

class ProfileDeleteView(LoginRequiredMixin, DeleteView):
    login_url = "register:login"
    model = Profile
    success_message = 'Profile deleted successfully!'
    template_name = 'profile_confirm_delete.html'  # Replace with your confirmation template

    def get_success_url(self):
        return reverse("register:profile_list")  # Replace with your list view URL
