package com.spotilinux.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.content.res.AssetFileDescriptor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.MediaMetadata;
import android.media.MediaPlayer;
import android.media.audiofx.Equalizer;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.view.KeyEvent;

import java.util.HashMap;
import java.util.Map;

public class MediaPlaybackService extends Service implements AudioManager.OnAudioFocusChangeListener {
    public static final String CHANNEL_ID = "spotilinux_media_channel";
    public static final int NOTIFICATION_ID = 2026;

    public static final String ACTION_PLAY_SOURCE = "com.spotilinux.mobile.ACTION_PLAY_SOURCE";
    public static final String ACTION_PLAY_PAUSE = "com.spotilinux.mobile.ACTION_PLAY_PAUSE";
    public static final String ACTION_PLAY = "com.spotilinux.mobile.ACTION_PLAY";
    public static final String ACTION_PAUSE = "com.spotilinux.mobile.ACTION_PAUSE";
    public static final String ACTION_NEXT = "com.spotilinux.mobile.ACTION_NEXT";
    public static final String ACTION_PREV = "com.spotilinux.mobile.ACTION_PREV";
    public static final String ACTION_SEEK = "com.spotilinux.mobile.ACTION_SEEK";
    public static final String ACTION_SET_REPEAT = "com.spotilinux.mobile.ACTION_SET_REPEAT";
    public static final String ACTION_SET_EQ = "com.spotilinux.mobile.ACTION_SET_EQ";
    public static final String ACTION_STOP = "com.spotilinux.mobile.ACTION_STOP";

    private static MediaPlaybackService instance;

    private MediaPlayer mediaPlayer;
    private Equalizer audioEqualizer;
    private MediaSession mediaSession;
    private AudioManager audioManager;
    private AudioFocusRequest audioFocusRequest;
    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;
    private Handler uiSyncHandler;

    private String currentTitle = "SpotiLinux Mobile";
    private String currentArtist = "Maomao & Cloud Edition";
    private String currentAlbum = "SpotiLinux";
    private String currentSource = "";
    private String currentAuthToken = "";
    private boolean isPlaying = false;
    private boolean isPrepared = false;
    private boolean repeatOne = false;
    private float[] currentEqGains = new float[]{0f, 0f, 0f, 0f, 0f};

    private final Runnable progressRunnable = new Runnable() {
        @Override
        public void run() {
            if (mediaPlayer != null && isPrepared && isPlaying) {
                try {
                    long pos = mediaPlayer.getCurrentPosition();
                    long dur = mediaPlayer.getDuration();
                    if (dur <= 0) dur = 28000L;
                    MainActivity.notifyWebProgress(pos, dur, true);
                } catch (Exception ignored) {
                }
            }
            if (uiSyncHandler != null) {
                uiSyncHandler.postDelayed(this, 250);
            }
        }
    };

    public static MediaPlaybackService getInstance() {
        return instance;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        createNotificationChannel();

        audioManager = (AudioManager) getSystemService(Context.AUDIO_SERVICE);

        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (pm != null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "SpotiLinux::MediaWakeLock");
            wakeLock.setReferenceCounted(false);
        }

        WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        if (wm != null) {
            wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "SpotiLinux::WifiLock");
            wifiLock.setReferenceCounted(false);
        }

        uiSyncHandler = new Handler(Looper.getMainLooper());
        uiSyncHandler.post(progressRunnable);

        initMediaPlayer();
        initMediaSession();
    }

    private void initMediaPlayer() {
        if (mediaPlayer != null) {
            try {
                mediaPlayer.release();
            } catch (Exception ignored) {
            }
        }
        mediaPlayer = new MediaPlayer();
        mediaPlayer.setAudioAttributes(
                new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build()
        );
        mediaPlayer.setWakeMode(getApplicationContext(), PowerManager.PARTIAL_WAKE_LOCK);

        mediaPlayer.setOnPreparedListener(new MediaPlayer.OnPreparedListener() {
            @Override
            public void onPrepared(MediaPlayer mp) {
                isPrepared = true;
                mp.setLooping(repeatOne);
                setupHardwareEqualizer(mp.getAudioSessionId());
                if (requestAudioFocus()) {
                    acquireLocks();
                    mp.start();
                    isPlaying = true;
                    syncEverything();
                }
            }
        });

        mediaPlayer.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
            @Override
            public void onCompletion(MediaPlayer mp) {
                if (repeatOne) {
                    mp.seekTo(0);
                    mp.start();
                    isPlaying = true;
                    syncEverything();
                } else {
                    MainActivity.dispatchMediaCommandToWeb("ended_next");
                }
            }
        });

        mediaPlayer.setOnErrorListener(new MediaPlayer.OnErrorListener() {
            @Override
            public boolean onError(MediaPlayer mp, int what, int extra) {
                isPrepared = false;
                isPlaying = false;
                syncEverything();
                return true;
            }
        });
    }

    private void setupHardwareEqualizer(int audioSessionId) {
        try {
            if (audioEqualizer != null) {
                audioEqualizer.release();
            }
            audioEqualizer = new Equalizer(0, audioSessionId);
            audioEqualizer.setEnabled(true);
            applyEqGainsToHardware();
        } catch (Exception ignored) {
        }
    }

    private void applyEqGainsToHardware() {
        if (audioEqualizer == null) return;
        try {
            short bands = audioEqualizer.getNumberOfBands();
            short[] range = audioEqualizer.getBandLevelRange();
            short minLevel = range[0]; // ej. -1500 mB (-15 dB)
            short maxLevel = range[1]; // ej. +1500 mB (+15 dB)
            for (short b = 0; b < bands && b < currentEqGains.length; b++) {
                int mB = Math.round(currentEqGains[b] * 100f);
                short clamped = (short) Math.max(minLevel, Math.min(maxLevel, mB));
                audioEqualizer.setBandLevel(b, clamped);
            }
        } catch (Exception ignored) {
        }
    }

    private void initMediaSession() {
        mediaSession = new MediaSession(this, "SpotiLinuxMediaSession");
        mediaSession.setFlags(
                MediaSession.FLAG_HANDLES_MEDIA_BUTTONS | MediaSession.FLAG_HANDLES_TRANSPORT_CONTROLS
        );

        Intent mediaButtonIntent = new Intent(Intent.ACTION_MEDIA_BUTTON);
        mediaButtonIntent.setClass(this, MediaPlaybackService.class);
        int piFlags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        PendingIntent mbrIntent = PendingIntent.getService(this, 99, mediaButtonIntent, piFlags);
        mediaSession.setMediaButtonReceiver(mbrIntent);

        mediaSession.setCallback(new MediaSession.Callback() {
            @Override
            public boolean onMediaButtonEvent(Intent mediaButtonIntent) {
                KeyEvent ke = mediaButtonIntent.getParcelableExtra(Intent.EXTRA_KEY_EVENT);
                if (ke != null && ke.getAction() == KeyEvent.ACTION_DOWN) {
                    int code = ke.getKeyCode();
                    if (code == KeyEvent.KEYCODE_MEDIA_PLAY) {
                        resumeNativePlayback();
                        return true;
                    } else if (code == KeyEvent.KEYCODE_MEDIA_PAUSE) {
                        pauseNativePlayback();
                        return true;
                    } else if (code == KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE || code == KeyEvent.KEYCODE_HEADSETHOOK) {
                        toggleNativePlayback();
                        return true;
                    } else if (code == KeyEvent.KEYCODE_MEDIA_NEXT) {
                        MainActivity.dispatchMediaCommandToWeb("next");
                        return true;
                    } else if (code == KeyEvent.KEYCODE_MEDIA_PREVIOUS) {
                        MainActivity.dispatchMediaCommandToWeb("prev");
                        return true;
                    }
                }
                return super.onMediaButtonEvent(mediaButtonIntent);
            }

            @Override
            public void onPlay() {
                resumeNativePlayback();
            }

            @Override
            public void onPause() {
                pauseNativePlayback();
            }

            @Override
            public void onSkipToNext() {
                MainActivity.dispatchMediaCommandToWeb("next");
            }

            @Override
            public void onSkipToPrevious() {
                MainActivity.dispatchMediaCommandToWeb("prev");
            }

            @Override
            public void onSeekTo(long pos) {
                seekNativePlayback((int) pos);
            }

            @Override
            public void onStop() {
                pauseNativePlayback();
            }
        });
        mediaSession.setActive(true);
    }

    private boolean requestAudioFocus() {
        if (audioManager == null) return true;
        int res;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            if (audioFocusRequest == null) {
                AudioAttributes attrs = new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build();
                audioFocusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
                        .setAudioAttributes(attrs)
                        .setAcceptsDelayedFocusGain(true)
                        .setOnAudioFocusChangeListener(this)
                        .build();
            }
            res = audioManager.requestAudioFocus(audioFocusRequest);
        } else {
            res = audioManager.requestAudioFocus(this, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN);
        }
        return res == AudioManager.AUDIOFOCUS_REQUEST_GRANTED;
    }

    @Override
    public void onAudioFocusChange(int focusChange) {
        if (focusChange == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT) {
            // Pausa temporal (ej. llamada o asistente), sin perder estado
            if (mediaPlayer != null && isPrepared && mediaPlayer.isPlaying()) {
                mediaPlayer.pause();
                isPlaying = false;
                syncEverything();
            }
        } else if (focusChange == AudioManager.AUDIOFOCUS_GAIN) {
            if (mediaPlayer != null && isPrepared) {
                mediaPlayer.setVolume(1.0f, 1.0f);
            }
        } else if (focusChange == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK) {
            if (mediaPlayer != null && isPrepared) {
                mediaPlayer.setVolume(0.25f, 0.25f);
            }
        }
    }

    private void acquireLocks() {
        try {
            if (wakeLock != null && !wakeLock.isHeld()) {
                wakeLock.acquire(6 * 60 * 60 * 1000L);
            }
            if (wifiLock != null && !wifiLock.isHeld()) {
                wifiLock.acquire();
            }
        } catch (Exception ignored) {
        }
    }

    public void loadAndPlaySource(String src, String title, String artist, String album, String authToken) {
        this.currentSource = src != null ? src : "";
        this.currentTitle = title != null ? title : "SpotiLinux";
        this.currentArtist = artist != null ? artist : "SpotiLinux Mobile";
        this.currentAlbum = album != null ? album : "SpotiLinux";
        this.currentAuthToken = authToken != null ? authToken : "";

        acquireLocks();
        if (mediaPlayer == null) {
            initMediaPlayer();
        }

        try {
            isPrepared = false;
            mediaPlayer.reset();
            mediaPlayer.setAudioAttributes(
                    new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                            .build()
            );

            if (currentSource.startsWith("tracks/")) {
                // Pista integrada en assets/www/tracks/...
                AssetFileDescriptor afd = getAssets().openFd("www/" + currentSource);
                mediaPlayer.setDataSource(afd.getFileDescriptor(), afd.getStartOffset(), afd.getLength());
                afd.close();
            } else if (currentSource.startsWith("http://") || currentSource.startsWith("https://")) {
                // Streaming en línea (ej. Google Drive con o sin Bearer Token)
                Map<String, String> headers = new HashMap<>();
                if (!currentAuthToken.isEmpty()) {
                    headers.put("Authorization", "Bearer " + currentAuthToken);
                }
                mediaPlayer.setDataSource(getApplicationContext(), Uri.parse(currentSource), headers);
            } else if (currentSource.startsWith("content://") || currentSource.startsWith("file://")) {
                mediaPlayer.setDataSource(getApplicationContext(), Uri.parse(currentSource));
            } else {
                // Ruta directa de archivo en la carpeta de la aplicación
                mediaPlayer.setDataSource(currentSource);
            }

            mediaPlayer.prepareAsync();
            isPlaying = true;
            syncEverything();
        } catch (Exception e) {
            isPlaying = false;
            syncEverything();
        }
    }

    public void toggleNativePlayback() {
        if (isPlaying) {
            pauseNativePlayback();
        } else {
            resumeNativePlayback();
        }
    }

    public void resumeNativePlayback() {
        acquireLocks();
        requestAudioFocus();
        if (mediaPlayer != null && isPrepared) {
            try {
                mediaPlayer.setVolume(1.0f, 1.0f);
                mediaPlayer.start();
                isPlaying = true;
                syncEverything();
                return;
            } catch (Exception ignored) {
            }
        }
        // Si aún no había pista preparada, recargar la actual o pedir a la UI que inicie la pista 0
        if (!currentSource.isEmpty()) {
            loadAndPlaySource(currentSource, currentTitle, currentArtist, currentAlbum, currentAuthToken);
        } else {
            MainActivity.dispatchMediaCommandToWeb("play");
        }
    }

    public void pauseNativePlayback() {
        if (mediaPlayer != null && isPrepared) {
            try {
                if (mediaPlayer.isPlaying()) {
                    mediaPlayer.pause();
                }
            } catch (Exception ignored) {
            }
        }
        isPlaying = false;
        // Importante: NO liberamos el WakeLock inmediatamente para que los audífonos Bluetooth
        // puedan reanudar la reproducción incluso tras varios minutos con la pantalla apagada.
        syncEverything();
    }

    public void seekNativePlayback(int posMs) {
        if (mediaPlayer != null && isPrepared) {
            try {
                mediaPlayer.seekTo(Math.max(0, posMs));
                syncEverything();
            } catch (Exception ignored) {
            }
        }
    }

    public void setRepeatMode(boolean loop) {
        this.repeatOne = loop;
        if (mediaPlayer != null && isPrepared) {
            try {
                mediaPlayer.setLooping(loop);
            } catch (Exception ignored) {
            }
        }
    }

    public void setEqBandGain(int bandIndex, float gainDb) {
        if (bandIndex >= 0 && bandIndex < currentEqGains.length) {
            currentEqGains[bandIndex] = gainDb;
            applyEqGainsToHardware();
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && intent.getAction() != null) {
            String action = intent.getAction();
            if (Intent.ACTION_MEDIA_BUTTON.equals(action)) {
                KeyEvent ke = intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT);
                if (ke != null && ke.getAction() == KeyEvent.ACTION_DOWN) {
                    int code = ke.getKeyCode();
                    if (code == KeyEvent.KEYCODE_MEDIA_PLAY) resumeNativePlayback();
                    else if (code == KeyEvent.KEYCODE_MEDIA_PAUSE) pauseNativePlayback();
                    else if (code == KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE || code == KeyEvent.KEYCODE_HEADSETHOOK) toggleNativePlayback();
                    else if (code == KeyEvent.KEYCODE_MEDIA_NEXT) MainActivity.dispatchMediaCommandToWeb("next");
                    else if (code == KeyEvent.KEYCODE_MEDIA_PREVIOUS) MainActivity.dispatchMediaCommandToWeb("prev");
                }
                return START_STICKY;
            } else if (ACTION_PLAY_SOURCE.equals(action)) {
                loadAndPlaySource(
                        intent.getStringExtra("src"),
                        intent.getStringExtra("title"),
                        intent.getStringExtra("artist"),
                        intent.getStringExtra("album"),
                        intent.getStringExtra("authToken")
                );
                return START_STICKY;
            } else if (ACTION_PLAY_PAUSE.equals(action)) {
                toggleNativePlayback();
                return START_STICKY;
            } else if (ACTION_PLAY.equals(action)) {
                resumeNativePlayback();
                return START_STICKY;
            } else if (ACTION_PAUSE.equals(action)) {
                pauseNativePlayback();
                return START_STICKY;
            } else if (ACTION_NEXT.equals(action)) {
                MainActivity.dispatchMediaCommandToWeb("next");
                return START_STICKY;
            } else if (ACTION_PREV.equals(action)) {
                if (mediaPlayer != null && isPrepared && mediaPlayer.getCurrentPosition() > 3200) {
                    seekNativePlayback(0);
                } else {
                    MainActivity.dispatchMediaCommandToWeb("prev");
                }
                return START_STICKY;
            } else if (ACTION_SEEK.equals(action)) {
                seekNativePlayback(intent.getIntExtra("posMs", 0));
                return START_STICKY;
            } else if (ACTION_SET_REPEAT.equals(action)) {
                setRepeatMode(intent.getBooleanExtra("repeat", false));
                return START_STICKY;
            } else if (ACTION_SET_EQ.equals(action)) {
                int idx = intent.getIntExtra("band", 0);
                float gain = intent.getFloatExtra("gain", 0f);
                setEqBandGain(idx, gain);
                return START_STICKY;
            } else if (ACTION_STOP.equals(action)) {
                pauseNativePlayback();
                stopForeground(true);
                stopSelf();
                return START_NOT_STICKY;
            }
        }

        syncEverything();
        return START_STICKY;
    }

    private void syncEverything() {
        long pos = 0L;
        long dur = 28000L;
        if (mediaPlayer != null && isPrepared) {
            try {
                pos = mediaPlayer.getCurrentPosition();
                dur = mediaPlayer.getDuration();
                if (dur <= 0) dur = 28000L;
            } catch (Exception ignored) {
            }
        }

        updateMediaSessionState(pos, dur);
        Notification notification = buildMediaNotification();

        try {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
        } catch (Exception e) {
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) {
                nm.notify(NOTIFICATION_ID, notification);
            }
        }

        MainActivity.notifyWebProgress(pos, dur, isPlaying);
    }

    private void updateMediaSessionState(long posMs, long durMs) {
        if (mediaSession == null) return;
        Bitmap art = BitmapFactory.decodeResource(getResources(), R.drawable.ic_launcher);

        MediaMetadata.Builder metaBuilder = new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, currentTitle)
                .putString(MediaMetadata.METADATA_KEY_ARTIST, currentArtist)
                .putString(MediaMetadata.METADATA_KEY_ALBUM, currentAlbum)
                .putLong(MediaMetadata.METADATA_KEY_DURATION, durMs);

        if (art != null) {
            metaBuilder.putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, art);
            metaBuilder.putBitmap(MediaMetadata.METADATA_KEY_DISPLAY_ICON, art);
        }
        mediaSession.setMetadata(metaBuilder.build());

        int state = isPlaying ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_PAUSED;
        float speed = isPlaying ? 1.0f : 0.0f;
        long actions = PlaybackState.ACTION_PLAY
                | PlaybackState.ACTION_PAUSE
                | PlaybackState.ACTION_PLAY_PAUSE
                | PlaybackState.ACTION_SKIP_TO_NEXT
                | PlaybackState.ACTION_SKIP_TO_PREVIOUS
                | PlaybackState.ACTION_SEEK_TO
                | PlaybackState.ACTION_STOP;

        PlaybackState playbackState = new PlaybackState.Builder()
                .setActions(actions)
                .setState(state, posMs, speed)
                .build();
        mediaSession.setPlaybackState(playbackState);
    }

    private Notification buildMediaNotification() {
        Intent openIntent = new Intent(this, MainActivity.class);
        openIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        int piFlags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        PendingIntent contentPendingIntent = PendingIntent.getActivity(this, 0, openIntent, piFlags);

        PendingIntent prevPending = PendingIntent.getService(
                this, 1, new Intent(this, MediaPlaybackService.class).setAction(ACTION_PREV), piFlags);
        PendingIntent playPausePending = PendingIntent.getService(
                this, 2, new Intent(this, MediaPlaybackService.class).setAction(ACTION_PLAY_PAUSE), piFlags);
        PendingIntent nextPending = PendingIntent.getService(
                this, 3, new Intent(this, MediaPlaybackService.class).setAction(ACTION_NEXT), piFlags);

        int playPauseIcon = isPlaying ? android.R.drawable.ic_media_pause : android.R.drawable.ic_media_play;
        String playPauseLabel = isPlaying ? "Pausar" : "Reproducir";

        Notification.Action prevAction = new Notification.Action.Builder(
                android.R.drawable.ic_media_previous, "Anterior", prevPending).build();
        Notification.Action playPauseAction = new Notification.Action.Builder(
                playPauseIcon, playPauseLabel, playPausePending).build();
        Notification.Action nextAction = new Notification.Action.Builder(
                android.R.drawable.ic_media_next, "Siguiente", nextPending).build();

        Bitmap largeIcon = BitmapFactory.decodeResource(getResources(), R.drawable.ic_launcher);

        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            builder = new Notification.Builder(this, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(this);
        }

        Notification.MediaStyle mediaStyle = new Notification.MediaStyle()
                .setMediaSession(mediaSession.getSessionToken())
                .setShowActionsInCompactView(0, 1, 2);

        builder.setContentTitle(currentTitle)
                .setContentText(currentArtist)
                .setSubText(currentAlbum)
                .setSmallIcon(isPlaying ? android.R.drawable.ic_media_play : android.R.drawable.ic_media_pause)
                .setLargeIcon(largeIcon)
                .setContentIntent(contentPendingIntent)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(isPlaying)
                .setOnlyAlertOnce(true)
                .setStyle(mediaStyle)
                .addAction(prevAction)
                .addAction(playPauseAction)
                .addAction(nextAction);

        return builder.build();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "Reproducción de Música SpotiLinux",
                    NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("Controles de reproducción en segundo plano, audífonos y bloqueo");
            channel.setShowBadge(false);
            channel.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) {
                nm.createNotificationChannel(channel);
            }
        }
    }

    @Override
    public void onDestroy() {
        instance = null;
        if (uiSyncHandler != null) {
            uiSyncHandler.removeCallbacksAndMessages(null);
        }
        if (audioEqualizer != null) {
            try {
                audioEqualizer.release();
            } catch (Exception ignored) {
            }
        }
        if (mediaPlayer != null) {
            try {
                mediaPlayer.release();
            } catch (Exception ignored) {
            }
        }
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        if (wifiLock != null && wifiLock.isHeld()) {
            wifiLock.release();
        }
        if (mediaSession != null) {
            mediaSession.setActive(false);
            mediaSession.release();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
