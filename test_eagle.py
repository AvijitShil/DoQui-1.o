"""Test Eagle in subprocess"""
import pveagle
import os
import traceback

os.chdir(r'c:\Users\aviji\OneDrive\Desktop\Projects\Xmas\Vienna')
access_key = 'I5HoBY5a5YNjCUcKVsvJzf//iQzLoyII6BO4tJeDsRIuyfqVq+v12w=='
profile_path = 'avijit_profile.eagle'

try:
    print('Loading profile...')
    with open(profile_path, 'rb') as f:
        profile_bytes = f.read()
    print(f'Profile loaded, {len(profile_bytes)} bytes')
    
    print('Creating EagleProfile...')
    profile = pveagle.EagleProfile.from_bytes(profile_bytes)
    print('EagleProfile created')
    
    print('Creating recognizer...')
    eagle = pveagle.create_recognizer(access_key=access_key, speaker_profiles=[profile])
    print('SUCCESS: Eagle works!')
    eagle.delete()
except Exception as e:
    print(f'ERROR: {e}')
    traceback.print_exc()
