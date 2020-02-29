#! /usr/bin/env python3

import time
import pigpio
import subprocess
import json

PI_HIGH = 1
PI_LOW = 0

# ------------------------------------------------------------------------------
# Pin assignment. Note that the pin numbers should be those mentioned in the
# Broadcom. In the comments the corresponding GPIO pin on the connector. For
# better understanding, the pin global constants are ordered by connector pin
# number.
# ------------------------------------------------------------------------------
pin_hangout_led = 4         # Hangout LED indicator (pin 7)
pin_ampli_mode = 15         # Amplifier mode button (pin 10)
pin_dial_counter = 18       # Count the dialed number (pin 12)
pin_dial_detect = 27        # Detect number dialing (pin 13)
pin_ampli_power = 22        # Amplifier power button (pin 15)
pin_phone_hangout = 23      # Phone hangup/hangout input (pin 16)
pin_dialer_led = 24         # Dialer counter LED (pin 19)
pin_dial_counter_led = 25   # On when the dialer is ready (pin 22)

# ------------------------------------------------------------------------------
# Global variables, flags and counters
# ------------------------------------------------------------------------------
cb_hangout_handler = 0      # Callback handler for the hangout switch
cb_dialer_handler = 0       # Callback handler for the rotary dialer
cb_counter_handler = 0      # Callback handler for the rotary pulse counter

# Status of the amplifier, or at least what it is expected to be. If the amplifier
# status is not corresponding there is a number to dial to reset the status
# accordingly with the pick-up switch detector.
ampli_status = False    # Become true when the amplifier is during a powering On/Off state

# Initially set to false it is True when the user start dialing a number with the
# rotary dialer. The status remain True until the rotary dialer has not completed the
# counterclockwise rotation emitting all the impulses corresponding to the dialled
# number.
dialer_status = False   # Become true when the user start dialing a number

# Pulse counter of the rotary dialer. WHen the dialer_status is high the
# counter is incrememnted while, when the dialer_status goes low the dialed
# number sequence is updated with the last number dialed.
pulses = 0

# Compound dialed number in string format. Until a number is not recognized as a command
# the further dialed numbers are queued to the string. It the number of characters of the
# dialed number reach the max lenght and has no meaning the number is reset and the counter
# restart to a new number.
dialed_number = ''

# Maximum number of characters of the dialed number.
# This depends on the numeric commands structures decided by the program. A three-number
# command code is sufficient for 999 different commands, maybe sufficient!
max_numbers = 3

DEBUG = False            # Set to False to remove the debug terminal output

# PiGPIO library instance, globally defined
pi = pigpio.pi()

# ------------------------------------------------------------------------------
# External commands and json parameter files
# ------------------------------------------------------------------------------
# Text-to-speech command and parameters
# Parameters: -sp = speak, -n = narrator voice (not used)
TTS = [ '/home/pi/smartphone/trans', '-sp' ]

# Mp3 play command and parameters. Volume can be a parameter of the command but in
# this case we only use the bare call to the player. The volume is set globally and is
# used by default.
PLAYER = ['mplayer']

# Cold reset command
REBOOT = ['sudo', 'reboot', 'now']

# Weather command
WEATHER = ['weather']

# Voice comments file
sentences_file = "/home/pi/smartphone/comments.json"
# Playlist file
music_file = "/home/pi/smartphone/playlist.json"

# ------------------------------------------------------------------------------
# Lists loaded on startup from the json files used to build the external
# commands calls.
# ------------------------------------------------------------------------------
# List with the messages for voice comments
text_messages = ['']
# Number of text messages
num_messages = 0

# Lists related to the music tracks
track_list = ['']       # List of the available tracks
track_titles = ['']     # Titles of the tracks
help_messages = ['']    # Help strings
help_strings = 0        # Number of help strings
tracks = 0              # Number of tracks
music_path = ''         # mp3 files path
track_position = 0      # Current playing track
is_playing = True       # Tracks playing status
weather_airport = ''    # Airport name in human readable format
weather_ICAO = ''       # Airport international ICAO code


def initGPIO():
    '''
    Initialize the GPIO library and set the callback for the interested
    pins with the corresponding function.
    Note that the Broadcom GPIO pin 28 should not be set to a callback
    to avoid problems.
    '''

    global pi
    global track_list
    global track_titles
    global tracks
    global songs
    global music_path
    global text_messages
    global sentences_file
    global help_messages
    global help_strings
    global weather_airport
    global weather_ICAO

    # Set the output pins
    pi.set_mode(pin_hangout_led, pigpio.OUTPUT)
    pi.set_mode(pin_ampli_mode, pigpio.OUTPUT)
    pi.set_mode(pin_ampli_power, pigpio.OUTPUT)
    pi.set_mode(pin_dialer_led, pigpio.OUTPUT)
    pi.set_mode(pin_dial_counter_led, pigpio.OUTPUT)

    # Set the input pins
    pi.set_mode(pin_dial_counter, pigpio.INPUT)
    pi.set_pull_up_down(pin_dial_counter, pigpio.PUD_DOWN)
    pi.set_mode(pin_dial_detect, pigpio.INPUT)
    pi.set_pull_up_down(pin_dial_detect, pigpio.PUD_DOWN)
    pi.set_mode(pin_phone_hangout, pigpio.INPUT)
    pi.set_pull_up_down(pin_phone_hangout, pigpio.PUD_DOWN)

    # Set the callback for the interested pins and gets the handlers
    set_callbacks()

    # Loads the music lists
    with open(music_file) as file:
        dictionary = json.load(file)

    track_list = dictionary['files']
    track_titles = dictionary['songs']
    tracks = int(dictionary['tracks'])
    music_path = dictionary['folder']

    # Loads the message tracks
    with open(sentences_file) as file:
        dictionary = json.load(file)

    num_messages = dictionary['phrases']
    help_strings = dictionary['helpsentences']
    text_messages = dictionary['list']
    help_messages = dictionary['help']
    weather_airport = dictionary['airport']
    weather_ICAO = dictionary['ICAO']

    reinit()


def set_callbacks():
    '''
    Enable the callback functions associated to the GPIO pins
    and save the callback handlers
    '''
    global pi
    global cb_counter_handler
    global cb_dialer_handler
    global cb_hangout_handler

    cb_hangout_handler = pi.callback(pin_phone_hangout, pigpio.EITHER_EDGE, hangout)
    cb_dialer_handler = pi.callback(pin_dial_detect, pigpio.EITHER_EDGE, dial_detect)
    cb_counter_handler = pi.callback(pin_dial_counter, pigpio.EITHER_EDGE, pulse_count)

    # LED high when the rotary is accepting numbers
    pi.write(pin_dial_counter_led, PI_HIGH)


def release_callbacks():
    '''
    Disable the callback functions
    '''
    global pi
    global cb_counter_handler
    global cb_dialer_handler
    global cb_hangout_handler

    cb_hangout_handler.cancel()
    cb_dialer_handler.cancel()
    cb_counter_handler.cancel()

    # LED high when the rotary is accepting numbers
    pi.write(pin_dial_counter_led, PI_LOW)


def cb_release_rotary():
    '''
    Cancel the specific callback functions (disable the interrupt)
    associated to the rotary dialer
    '''
    global pi
    global cb_counter_handler
    global cb_dialer_handler

    cb_counter_handler.cancel()
    cb_dialer_handler.cancel()

    # LED high when the rotary is accepting numbers
    pi.write(pin_dial_counter_led, PI_LOW)


def cb_set_rotary():
    '''
    Enable the specific callback functions (disable the interrupt)
    associated to the rotary dialer
    '''
    global pi
    global cb_counter_handler
    global cb_dialer_handler

    cb_dialer_handler = pi.callback(pin_dial_detect, pigpio.EITHER_EDGE, dial_detect)
    cb_counter_handler = pi.callback(pin_dial_counter, pigpio.EITHER_EDGE, pulse_count)

    # LED high when the rotary is accepting numbers
    pi.write(pin_dial_counter_led, PI_HIGH)


def cb_set_hangout():
    '''
    Set the hangout callback
    '''
    global pi
    global cb_hangout_handler

    cb_hangout_handler = pi.callback(pin_phone_hangout, pigpio.EITHER_EDGE, hangout)


def debug_message(message, value = 0, message_only = True):
    '''
    Print the debug message if the debug flag is set
    '''

    if(DEBUG == True):
        if message_only is not True:
            print(message, value)
        else:
            print(message)


def play_all_tracks():
    '''
    Play all the tracks on the playlist.json file
    after powering the amplifier and announing every track title.
    Then power off the amplifier.
    '''
    global track_position
    global is_playing
    global music_path
    global tracks

    # Enable the amplifier and set the playing flag
    is_playing = True
    ampli_on_off()

    # Start from the first track of the playlist
    track_position = 0

    # Announce the initial message
    txt = text_messages[3] + ' ' + text_messages[2]
    runCmd([TTS[0], TTS[1], txt])

    # Play tracks until the playlist ends
    while track_position < tracks:

        debug_message('pos ' + str(track_position) + ' tracks ' + str(tracks))

        # Announce the name of the track
        txt = text_messages[4] + ' ' + track_titles[track_position]
        runCmd([TTS[0], TTS[1], txt])

        # Create the track file name and play it
        txt = music_path + track_list[track_position] + '.mp3'
        runCmd([PLAYER[0], txt])

        # Update the track number
        track_position += 1

        # Stop the playing loop if the user hangout
        if pi.read(pin_phone_hangout) is PI_LOW:
            break;

    # Disabe the amplifier, if it has not yet disabled by the user
    if is_playing is True:
        ampli_on_off()
        is_playing = False


def list_all_tracks():
    '''
    Tell all the tracks on the playlist.json file
    after powering the amplifier and saying every track title.
    Then power off the amplifier.
    '''
    global is_playing
    global tracks
    global track_titles

    # Enable the amplifier and set the playing flag
    is_playing = True
    ampli_on_off()

    # Announce the initial message
    txt = text_messages[6] + ' ' + str(tracks) + ' ' + text_messages[7]
    runCmd([TTS[0], TTS[1], txt])

    counter = 0     # Playlist title counter

    # Play tracks until the playlist ends
    while counter < tracks:

        # Announce the name of the track
        txt = ' ' + str(counter + 1) + ': ' + track_titles[counter] + ", "
        runCmd([TTS[0], TTS[1], txt])

        # Update the title number
        counter += 1

    # Disabe the amplifier, if it has not yet disabled by the user
    if is_playing is True:
        ampli_on_off()
        is_playing = False


def say_help():
    '''
    Tell the help notes.
    '''
    global is_playing
    global help_strings
    global help_messages

    # Enable the amplifier and set the playing flag
    is_playing = True
    ampli_on_off()

    counter = 0     # Playlist title counter

    # Play messages
    while counter < help_strings:

        txt = help_messages[counter]
        runCmd([TTS[0], TTS[1], txt])

        # Update the title number
        counter += 1

    # Disabe the amplifier, if it is not yet disabled by the user
    if is_playing is True:
        ampli_on_off()
        is_playing = False


def play_track():
    '''
    Play a track based on the parameters of the playlist.json file
    after powering the amplifier and announcing the track title.
    Then power off the amplifier.
    '''
    global track_position
    global is_playing
    global music_path
    global tracks

    # Enable the amplifier and set the playing flag
    is_playing = True
    ampli_on_off()

    # Announce the name of the track
    txt = text_messages[4] + ' ' + track_titles[track_position]
    runCmd([TTS[0], TTS[1], txt])

    # Create the track file name and play it
    txt = music_path + track_list[track_position] + '.mp3'
    runCmd([PLAYER[0], txt])

    # Increment the number of the track and if the value is bigger
    # than  the max number of tracks it is reset.
    track_position += 1
    if track_position == tracks:
        track_position = 0

    # Disabe the amplifier, if it has not yet disabled by the user
    if is_playing is True:
        ampli_on_off()
        is_playing = False


def tts_message(msg):
    '''
    Prepare che message msg to be played as an audio command
    :param msg: The message code accordingly with the list in the
    json file
    :return: 0 or the tts bash command execution error code
    '''

    # Create the full text message
    tText = text_messages[msg]

    return runCmd([TTS[0], TTS[1], tText])


def runCmd(cmd, extra_info = False):
    '''
    Execute a subprocess command managing the return value, stdout, stderr
    and the return code (0 or not 0 if error occurred)
    :param cmd: The bash command with the parameters
    :return: 0 or the error returncode
    '''

    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            )
    stdout, stderr = proc.communicate()

    return proc.returncode # , stdout, stderr


def get_weather():
    '''
    Retrieve the weather from the nearest airport. The desired international
    airport weather station ICAO four character code should be set in the
    comments.json file.
    The command speak the weather data returned from the call.
    '''
    global is_playing
    global weather_airport
    global weather_ICAO

    # Enable the amplifier and set the playing flag
    is_playing = True
    ampli_on_off()

    # Announce the weather retrieval
    runCmd([TTS[0], TTS[1], weather_airport])

    # Execute the weather command
    cmd = [WEATHER[0], weather_ICAO]

    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            )
    stdout, stderr = proc.communicate()
    # Divide the weather message in a list of single lines
    # removing the newline characters
    forecast = stdout.splitlines()

    w = 4   # First useful line of the weather forecase

    # Say the forecast meaningful strings (starting from 4)
    # Note that forecast is a list of bytes so every text line
    # should be decoded to the corresponding ASCII string
    while w < len(forecast):
        text = forecast[w].decode('ascii')
        runCmd([TTS[0], TTS[1], text])
        w += 1

    # Disabe the amplifier, if it has not yet disabled by the user
    if is_playing is True:
        ampli_on_off()
        is_playing = False


def wrong_command():
    '''
    Says wrong command if the dialed number is invalid
    '''
    global is_playing

    # Enable the amplifier and set the playing flag
    is_playing = True
    ampli_on_off()

    # Announce the weather retrieval
    runCmd([TTS[0], TTS[1], text_messages[8]])

    # Disabe the amplifier, if it has not yet disabled by the user
    if is_playing is True:
        ampli_on_off()
        is_playing = False


def play_tts_sentence(msg):
    '''
    Play a text message. Call this command only outside of the playing track
    as it enables and disables the amplifier by itself.
    :param msg: The message code accordingly with the list in the
    json file
    '''
    global is_playing

    # Enable the amplifier and set the playing flag
    is_playing = True
    ampli_on_off()

    tts_message(msg)

    # Disabe the amplifier, if it has not yet disabled by the user
    if is_playing is True:
        ampli_on_off()
        is_playing = False


def hangout(self, event, tick):
    '''
    Callback function.
    Detect the hangon/hangoff switch of the pickup.
    The status of the switch will power the amplifier accordingly
    when needed. When the function is called the LED is set accordingly
    to the status of the pin
    '''
    global pi
    global is_playing

    if pi.read(pin_phone_hangout) is PI_HIGH:
        # Disable the interrupts until finished
        release_callbacks()
        # Show the ready LED
        pi.write(pin_hangout_led, PI_HIGH)
        # Activation message
        play_tts_sentence(0)
        # Enable the interrupts
        set_callbacks()

    else:
        # Disable the interrupts until finished
        release_callbacks()
        # End message
        play_tts_sentence(1)
        # Disable the ready LED
        pi.write(pin_hangout_led, PI_LOW)
        # Enable only the hangout interrupt
        reinit()
        cb_set_hangout()


def dial_detect(self, event, tick):
    '''
    Manage the dialer pulses to encode the dialed number
    then the number is queued to the string collecting the numbers
    processed by the main program.
    The dialer works only if the pick up is open.
    '''
    global dialer_status
    global pulses
    global max_numbers
    global dialed_number
    global pi

    if pi.read(pin_phone_hangout) is PI_HIGH:
        # Detect if the user started dialing a number
        dialer_status = pi.read(pin_dial_detect)

        # Check if a number is to be dialed
        if dialer_status is PI_HIGH:
            # Reset the pulses counter
            pulses = 0
            pi.write(pin_dialer_led, PI_HIGH)

        # Check if the dialed number has reached the maximum length
        # to reset the number and start a new queue
        if len(dialed_number) >= max_numbers:
            dialed_number = ''      # Reset the number sequence
        else:
            # Queue the dialed number
            if pulses is not 0:
                # When the cipher 0 is dialed the rotary wheel
                # emit 10 pulses
                if pulses == 20:
                    pulses = 0

                dialed_number = dialed_number + str(int(pulses / 2))
                # Check if the user has dialed a valid number associated to
                # a command.
                check_number()

        pi.write(pin_dialer_led, PI_LOW)


def check_number():
    '''
    Check if the current number corresponds to a valid command.
    '''
    global dialed_number
    global track_position
    global pi

    debug_message(str(dialed_number))

    # Numeric commands and related functions
    if dialed_number is not '':
        if int(dialed_number) == 666:
            # Restart the appplication to initial conditions
            dialed_number = ''
            reinit()

        # Play the next track
        elif int(dialed_number) == 321:
            # Disable the interrupts until finished
            release_callbacks()
            # Start playing the next track
            play_track()
            # Enable the interrupts
            cb_set_hangout()
            # If the hangout is still active, enable the dialer too
            if pi.read(pin_hangout_led) is PI_HIGH:
                cb_set_rotary()

        # Play all tracks in sequence
        elif int(dialed_number) == 123:
            # Disable the interrupts until finished
            release_callbacks()
            # Play the entire playlist
            play_all_tracks()
            # Enable the interrupts
            cb_set_hangout()
            # If the hangout is still active, enable the dialer too
            if pi.read(pin_hangout_led) is PI_HIGH:
                cb_set_rotary()

        # Tell the playlist titles
        elif int(dialed_number) == 124:
            # Disable the interrupts until finished
            release_callbacks()
            # Tell the playlist titles
            list_all_tracks()
            # Enable the interrupts
            cb_set_hangout()
            # If the hangout is still active, enable the dialer too
            if pi.read(pin_hangout_led) is PI_HIGH:
                cb_set_rotary()

        # Play the desired track
        elif (int(dialed_number) <= tracks + 400) and (int(dialed_number) > 400):
            track_position = int(dialed_number) - 401
            # Disable the interrupts until finished
            release_callbacks()
            # Start playing the next track
            play_track()
            # Enable the interrupts
            cb_set_hangout()
            # If the hangout is still active, enable the dialer too
            if pi.read(pin_hangout_led) is PI_HIGH:
                cb_set_rotary()

        # Tell the help notes
        elif int(dialed_number) == 111:
            # Disable the interrupts until finished
            release_callbacks()
            # Tell the help messages
            say_help()
            # Enable the interrupts
            cb_set_hangout()
            # If the hangout is still active, enable the dialer too
            if pi.read(pin_hangout_led) is PI_HIGH:
                cb_set_rotary()

        elif int(dialed_number) == 999:
            # Reboot the system
            runCmd([REBOOT[0], REBOOT[1], REBOOT[2]])

        # Tell the weather
        elif int(dialed_number) == 100:
            # Disable the interrupts until finished
            release_callbacks()
            # Retrieve and say the weather message
            get_weather()
            # Enable the interrupts
            cb_set_hangout()
            # If the hangout is still active, enable the dialer too
            if pi.read(pin_hangout_led) is PI_HIGH:
                cb_set_rotary()

        elif int(dialed_number) == 999:
            # Reboot the system
            runCmd([REBOOT[0], REBOOT[1], REBOOT[2]])

        # else:
        #     # Wrong command message
        #     wrong_command()


def reinit():
    '''
    Restart the appplication to initial conditions
    '''
    global pi
    global dialed_number

    # Reset the LEDs
    pi.write(pin_dial_counter_led, PI_LOW)
    pi.write(pin_hangout_led, PI_LOW)
    pi.write(pin_dialer_led, PI_LOW)
    # Reset the dialed number
    dialed_number = ''
    track_position = 0
    is_playing = True


def pulse_count(self, event, tick):
    '''
    Count the pulses when the user dials a number. Only the HIGH values are
    considered as pulses and the counter increase the number only when the
    dialer_status value is HIGH.

    Note that the number of pulses is doubled as the interrupt
    read both the transitions to and from high.
    '''
    global pulses

    if dialer_status is PI_HIGH:
        pulses += 1


def ampli_on_off():
    '''
    Amplifier buttons simulator. Simulate the amplifier buttons for power on and
    setting the input mode to the cable input.
    NOTE: due to the charateristics of the amplifier we does not know if changing the
    power on/off button really powers it on or off. If the logic become inverted,
    there is a specific dial code that press the power button, independently by
    the status and the state of the pick-up switch status.
    '''

    global ampli_status
    global pi

    # Power on/off the ampli
    pi.write(pin_ampli_power, PI_HIGH)
    time.sleep(5)
    pi.write(pin_ampli_power, PI_LOW)
    time.sleep(2)
    # Set mode to direct plug audio
    pi.write(pin_ampli_mode, PI_HIGH)
    time.sleep(0.5)
    pi.write(pin_ampli_mode, PI_LOW)
    ampli_status = False


if __name__ == '__main__':
    # Main application
    initGPIO()
    
    # looping infinitely
    while True:
        pass
