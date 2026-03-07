# register/context_processors.py

def user_info(request):
    """
    Makes specific user data and role flags available globally in all templates.
    """
    # 1. Default fallback for users who are NOT logged in
    context = {
        'is_hr_assistant': False,
        'user_display_name': 'Guest',
    }

    # 2. Add real data if the user IS logged in
    if request.user.is_authenticated:
        # Example: Check if the user is in a specific group
        is_hr = request.user.groups.filter(name='Human Resource Assistant').exists()
        
        context.update({
            'is_hr_assistant': is_hr,
            'user_display_name': request.user.get_full_name() or request.user.username,
        })

    # 3. Always return a dictionary
    return context