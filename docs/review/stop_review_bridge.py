print('Review credential runner shutdown scheduled for its own process only.')
threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
