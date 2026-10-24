package com.spotilinux.mobile;

import android.Manifest;
import android.app.Activity;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.media.MediaMetadataRetriever;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.MediaStore;
import android.provider.OpenableColumns;
import android.util.Base64;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {
    private static MainActivity instance;
    /** true mientras la pantalla de la app está visible: solo entonces se envía el espectro al WebView (ahorra batería). */
    public static volatile boolean uiVisible = true;
    private WebView webView;
    private ValueCallback<Uri[]> filePathCallback;
    private static final int FILE_CHOOSER_RESULT_CODE = 1001;
    private static final int PERMISSIONS_REQUEST_CODE = 1002;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        instance = this;

        Window window = getWindow();
        window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
        window.setStatusBarColor(Color.parseColor("#000000"));
        window.setNavigationBarColor(Color.parseColor("#000000"));

        requestNecessaryPermissions();

        webView = new WebView(this);
        webView.setBackgroundColor(Color.parseColor("#000000"));

        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setAllowFileAccessFromFileURLs(true);
        settings.setAllowUniversalAccessFromFileURLs(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        // User-Agent compatible con inicio de sesión OAuth de Google
        settings.setUserAgentString(
                "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
        );

        webView.addJavascriptInterface(new AndroidMediaBridge(), "AndroidMedia");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                android.util.Log.e("SpotiLinuxWeb", "Error al cargar " + failingUrl + ": " + description);
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(android.webkit.ConsoleMessage consoleMessage) {
                android.util.Log.d("SpotiLinuxJS", consoleMessage.message() + " (" + consoleMessage.sourceId() + ":" + consoleMessage.lineNumber() + ")");
                return true;
            }

            @Override
            public boolean onShowFileChooser(
                    WebView webView,
                    ValueCallback<Uri[]> filePathCallback,
                    FileChooserParams fileChooserParams) {
                if (MainActivity.this.filePathCallback != null) {
                    MainActivity.this.filePathCallback.onReceiveValue(null);
                }
                MainActivity.this.filePathCallback = filePathCallback;

                Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("audio/*");
                intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);

                try {
                    startActivityForResult(Intent.createChooser(intent, "Seleccionar música"), FILE_CHOOSER_RESULT_CODE);
                } catch (Exception e) {
                    MainActivity.this.filePathCallback = null;
                    return false;
                }
                return true;
            }
        });

        setContentView(webView);
        webView.loadUrl("file:///android_asset/www/index.html");
    }

    private void requestNecessaryPermissions() {
        List<String> perms = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33) {
            if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                perms.add(Manifest.permission.POST_NOTIFICATIONS);
            }
            if (checkSelfPermission(Manifest.permission.READ_MEDIA_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                perms.add(Manifest.permission.READ_MEDIA_AUDIO);
            }
        } else {
            if (checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                perms.add(Manifest.permission.READ_EXTERNAL_STORAGE);
            }
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            // Solo para analizar el espectro de la música de la propia app (Visualizer). No se graba el micrófono.
            perms.add(Manifest.permission.RECORD_AUDIO);
        }
        if (!perms.isEmpty()) {
            requestPermissions(perms.toArray(new String[0]), PERMISSIONS_REQUEST_CODE);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSIONS_REQUEST_CODE) {
            MediaPlaybackService svc = MediaPlaybackService.getInstance();
            if (svc != null) {
                svc.retryVisualizer();
            }
            // Al otorgar permisos de audio, rastrear automáticamente los archivos del dispositivo
            new Thread(new Runnable() {
                @Override
                public void run() {
                    final String json = performDeviceScanAndSave();
                    dispatchJs("if(window.onDeviceMusicScanned) window.onDeviceMusicScanned(" + json + ");");
                }
            }).start();
        }
    }

    /**
     * Carpeta persistente propia de la aplicación donde se guardan todas las canciones
     * importadas, rastreadas del teléfono o descargadas de Google Drive:
     * /storage/emulated/0/Android/data/com.spotilinux.mobile/files/SpotiLinuxMusic
     */
    public File getAppMusicFolder() {
        File base = getExternalFilesDir(null);
        if (base == null) {
            base = getFilesDir();
        }
        File folder = new File(base, "SpotiLinuxMusic");
        if (!folder.exists()) {
            folder.mkdirs();
        }
        return folder;
    }

    private String sanitizeFilename(String name) {
        if (name == null || name.trim().isEmpty()) return "track_" + System.currentTimeMillis() + ".mp3";
        String clean = name.replaceAll("[\\\\/:*?\"<>|]", "_").trim();
        if (!clean.contains(".")) {
            clean += ".mp3";
        }
        return clean;
    }

    private String normalizeTrackKey(String name) {
        if (name == null) return "";
        String clean = name.replaceFirst("\\.[^.]+$", "");
        if (clean.startsWith("gd_") && clean.contains("__")) {
            clean = clean.substring(clean.indexOf("__") + 2);
        } else if (clean.startsWith("gd_")) {
            clean = clean.substring(3);
        }
        return clean.toLowerCase().replaceAll("[^a-z0-9]+", "");
    }

    private String buildSavedTracksJson() {
        JSONArray arr = new JSONArray();
        File folder = getAppMusicFolder();
        File[] files = folder.listFiles();
        if (files != null) {
            for (File f : files) {
                if (f.isFile() && f.length() > 1024) {
                    String fname = f.getName();
                    String lower = fname.toLowerCase();
                    if (lower.endsWith(".mp3") || lower.endsWith(".flac") || lower.endsWith(".wav")
                            || lower.endsWith(".m4a") || lower.endsWith(".ogg") || lower.endsWith(".opus") || lower.endsWith(".aac")) {
                        try {
                            JSONObject obj = new JSONObject();
                            String stem = fname.replaceFirst("\\.[^.]+$", "");
                            boolean fromDrive = fname.startsWith("gd_");
                            String canonicalId = "saved_" + Math.abs(f.getAbsolutePath().hashCode());
                            String driveId = "";
                            if (fromDrive && stem.contains("__")) {
                                int sep = stem.indexOf("__");
                                canonicalId = stem.substring(0, sep);
                                driveId = canonicalId.startsWith("gd_") ? canonicalId.substring(3) : "";
                                stem = stem.substring(sep + 2);
                            } else if (fromDrive && stem.startsWith("gd_")) {
                                stem = stem.substring(3);
                            }
                            stem = stem.replace("_", " ").trim();
                            String artist = fromDrive ? "Google Drive (Descargada) ✔" : "Carpeta SpotiLinux 📱";
                            String title = stem;
                            if (stem.contains(" - ")) {
                                String[] parts = stem.split(" - ", 2);
                                artist = parts[0].trim();
                                title = parts[1].trim();
                            }
                            obj.put("id", canonicalId);
                            if (!driveId.isEmpty()) {
                                obj.put("driveId", driveId);
                            }
                            obj.put("title", title);
                            obj.put("artist", artist);
                            obj.put("album", fromDrive ? "Google Drive Offline" : "Biblioteca Guardada");
                            obj.put("genre", fromDrive ? "Cloud Offline" : "Local");
                            obj.put("src", f.getAbsolutePath());
                            obj.put("savedPath", f.getAbsolutePath());
                            obj.put("cloud", fromDrive);
                            obj.put("downloaded", true);

                            // Extraer metadatos ID3 y carátula incrustada si existen
                            MediaMetadataRetriever mmr = null;
                            try {
                                mmr = new MediaMetadataRetriever();
                                mmr.setDataSource(f.getAbsolutePath());
                                String mTitle = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_TITLE);
                                String mArtist = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ARTIST);
                                String mAlbum = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_ALBUM);
                                String mGenre = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_GENRE);

                                if (mTitle != null && !mTitle.trim().isEmpty()) {
                                    obj.put("title", mTitle.trim());
                                }
                                if (mArtist != null && !mArtist.trim().isEmpty() && !mArtist.equalsIgnoreCase("<unknown>")) {
                                    obj.put("artist", mArtist.trim());
                                }
                                if (mAlbum != null && !mAlbum.trim().isEmpty()) {
                                    obj.put("album", mAlbum.trim());
                                }
                                if (mGenre != null && !mGenre.trim().isEmpty()) {
                                    obj.put("genre", mGenre.trim());
                                }

                                byte[] rawArt = mmr.getEmbeddedPicture();
                                if (rawArt != null && rawArt.length > 0) {
                                    BitmapFactory.Options opts = new BitmapFactory.Options();
                                    opts.inJustDecodeBounds = true;
                                    BitmapFactory.decodeByteArray(rawArt, 0, rawArt.length, opts);
                                    int sample = 1;
                                    while ((opts.outWidth / sample) > 256 || (opts.outHeight / sample) > 256) {
                                        sample *= 2;
                                    }
                                    opts.inJustDecodeBounds = false;
                                    opts.inSampleSize = sample;
                                    Bitmap bmp = BitmapFactory.decodeByteArray(rawArt, 0, rawArt.length, opts);
                                    if (bmp != null) {
                                        File coversDir = new File(getCacheDir(), "covers");
                                        if (!coversDir.exists()) coversDir.mkdirs();
                                        String hash = Integer.toHexString(f.getAbsolutePath().hashCode());
                                        File coverFile = new File(coversDir, hash + ".jpg");
                                        if (!coverFile.exists() || coverFile.length() < 100) {
                                            try (FileOutputStream fos = new FileOutputStream(coverFile)) {
                                                bmp.compress(Bitmap.CompressFormat.JPEG, 80, fos);
                                            } catch (Throwable ignored) {
                                            }
                                        }
                                        if (coverFile.exists() && coverFile.length() > 0) {
                                            obj.put("cover", "file://" + coverFile.getAbsolutePath());
                                        }
                                        bmp.recycle();
                                    }
                                }
                            } catch (Throwable ignored) {
                            } finally {
                                if (mmr != null) {
                                    try { mmr.release(); } catch (Throwable ignored) {}
                                }
                            }
                            arr.put(obj);
                        } catch (Exception ignored) {
                        }
                    }
                }
            }
        }
        return arr.toString();
    }

    private String performDeviceScanAndSave() {
        File destFolder = getAppMusicFolder();
        ContentResolver resolver = getContentResolver();
        Uri uri = MediaStore.Audio.Media.EXTERNAL_CONTENT_URI;
        String[] projection = new String[]{
                MediaStore.Audio.Media._ID,
                MediaStore.Audio.Media.TITLE,
                MediaStore.Audio.Media.ARTIST,
                MediaStore.Audio.Media.DISPLAY_NAME
        };

        try (Cursor cursor = resolver.query(uri, projection, null, null, MediaStore.Audio.Media.DATE_ADDED + " DESC")) {
            if (cursor != null) {
                int idCol = cursor.getColumnIndexOrThrow(MediaStore.Audio.Media._ID);
                int titleCol = cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.TITLE);
                int artistCol = cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.ARTIST);
                int nameCol = cursor.getColumnIndexOrThrow(MediaStore.Audio.Media.DISPLAY_NAME);

                int count = 0;
                while (cursor.moveToNext() && count < 150) {
                    long id = cursor.getLong(idCol);
                    String title = cursor.getString(titleCol);
                    String artist = cursor.getString(artistCol);
                    String displayName = cursor.getString(nameCol);

                    String ext = ".mp3";
                    if (displayName != null && displayName.contains(".")) {
                        ext = displayName.substring(displayName.lastIndexOf('.')).toLowerCase();
                    }

                    String baseName;
                    if (artist != null && !artist.equals("<unknown>") && title != null && !title.isEmpty()) {
                        baseName = artist + " - " + title + ext;
                    } else if (displayName != null && !displayName.isEmpty()) {
                        baseName = displayName;
                    } else {
                        baseName = "Track_" + id + ext;
                    }

                    File targetFile = new File(destFolder, sanitizeFilename(baseName));
                    if (!targetFile.exists() || targetFile.length() < 1024) {
                        Uri trackUri = ContentUris.withAppendedId(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, id);
                        copyUriToFile(trackUri, targetFile);
                    }
                    count++;
                }
            }
        } catch (Exception ignored) {
        }

        return buildSavedTracksJson();
    }

    private boolean copyUriToFile(Uri uri, File targetFile) {
        try (InputStream in = getContentResolver().openInputStream(uri);
             FileOutputStream out = new FileOutputStream(targetFile)) {
            if (in == null) return false;
            byte[] buf = new byte[65536];
            int len;
            while ((len = in.read(buf)) > 0) {
                out.write(buf, 0, len);
            }
            return targetFile.exists() && targetFile.length() > 512;
        } catch (Exception e) {
            return false;
        }
    }

    private String getDisplayNameFromUri(Uri uri) {
        String result = null;
        if ("content".equals(uri.getScheme())) {
            try (Cursor cursor = getContentResolver().query(uri, null, null, null, null)) {
                if (cursor != null && cursor.moveToFirst()) {
                    int idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                    if (idx >= 0) result = cursor.getString(idx);
                }
            } catch (Exception ignored) {
            }
        }
        if (result == null) {
            result = uri.getLastPathSegment();
        }
        return sanitizeFilename(result);
    }

    public class AndroidMediaBridge {
        @JavascriptInterface
        public void playSource(String src, String title, String artist, String album, String authToken) {
            Intent serviceIntent = new Intent(MainActivity.this, MediaPlaybackService.class);
            serviceIntent.setAction(MediaPlaybackService.ACTION_PLAY_SOURCE);
            serviceIntent.putExtra("src", src);
            serviceIntent.putExtra("title", title);
            serviceIntent.putExtra("artist", artist);
            serviceIntent.putExtra("album", album);
            serviceIntent.putExtra("authToken", authToken != null ? authToken : "");
            startMediaServiceSafely(serviceIntent);
        }

        @JavascriptInterface
        public void togglePlayPause() {
            MediaPlaybackService svc = MediaPlaybackService.getInstance();
            if (svc != null) {
                svc.toggleNativePlayback();
                return;
            }
            Intent serviceIntent = new Intent(MainActivity.this, MediaPlaybackService.class);
            serviceIntent.setAction(MediaPlaybackService.ACTION_PLAY_PAUSE);
            startMediaServiceSafely(serviceIntent);
        }

        @JavascriptInterface
        public void seekTo(int posMs) {
            MediaPlaybackService svc = MediaPlaybackService.getInstance();
            if (svc != null) {
                svc.seekNativePlayback(posMs);
                return;
            }
            Intent serviceIntent = new Intent(MainActivity.this, MediaPlaybackService.class);
            serviceIntent.setAction(MediaPlaybackService.ACTION_SEEK);
            serviceIntent.putExtra("posMs", posMs);
            startMediaServiceSafely(serviceIntent);
        }

        @JavascriptInterface
        public void setRepeat(boolean repeat) {
            MediaPlaybackService svc = MediaPlaybackService.getInstance();
            if (svc != null) {
                svc.setRepeatMode(repeat);
                return;
            }
            Intent serviceIntent = new Intent(MainActivity.this, MediaPlaybackService.class);
            serviceIntent.setAction(MediaPlaybackService.ACTION_SET_REPEAT);
            serviceIntent.putExtra("repeat", repeat);
            startMediaServiceSafely(serviceIntent);
        }

        @JavascriptInterface
        public void setEqBand(int band, float gainDb) {
            MediaPlaybackService svc = MediaPlaybackService.getInstance();
            if (svc != null) {
                svc.setEqBandGain(band, gainDb);
                return;
            }
            Intent serviceIntent = new Intent(MainActivity.this, MediaPlaybackService.class);
            serviceIntent.setAction(MediaPlaybackService.ACTION_SET_EQ);
            serviceIntent.putExtra("band", band);
            serviceIntent.putExtra("gain", gainDb);
            startMediaServiceSafely(serviceIntent);
        }

        @JavascriptInterface
        public String getSavedTracks() {
            try {
                return buildSavedTracksJson();
            } catch (Throwable t) {
                return "[]";
            }
        }

        @JavascriptInterface
        public String getAppMusicFolderPath() {
            return getAppMusicFolder().getAbsolutePath();
        }

        @JavascriptInterface
        public void scanDeviceMusicAsync() {
            requestNecessaryPermissions();
            new Thread(new Runnable() {
                @Override
                public void run() {
                    final String json = performDeviceScanAndSave();
                    dispatchJs("if(window.onDeviceMusicScanned) window.onDeviceMusicScanned(" + json + ");");
                }
            }).start();
        }

        @JavascriptInterface
        public void downloadDriveTrack(final String fileId, final String title, final String authToken, final String apiKey) {
            new Thread(new Runnable() {
                @Override
                public void run() {
                    try {
                        File folder = getAppMusicFolder();
                        String targetNorm = normalizeTrackKey(title);
                        File[] existingFiles = folder.listFiles();
                        if (existingFiles != null) {
                            for (File ef : existingFiles) {
                                if (ef.isFile() && ef.length() > 1024) {
                                    String fn = ef.getName();
                                    boolean sameId = fn.startsWith("gd_" + fileId + "__") || fn.startsWith("gd_" + fileId + "_");
                                    boolean sameTitle = !targetNorm.isEmpty() && normalizeTrackKey(fn).equals(targetNorm);
                                    if (sameId || sameTitle) {
                                        String escPath = ef.getAbsolutePath().replace("\\", "\\\\").replace("'", "\\'");
                                        String escId = fileId.replace("'", "\\'");
                                        dispatchJs("if(window.onDriveTrackDownloaded) window.onDriveTrackDownloaded('" + escId + "', '" + escPath + "', true, true);");
                                        return;
                                    }
                                }
                            }
                        }

                        String safeName = sanitizeFilename("gd_" + fileId + "__" + title + ".mp3");
                        File outFile = new File(folder, safeName);

                        String urlStr = "https://www.googleapis.com/drive/v3/files/" + fileId + "?alt=media";
                        if (apiKey != null && !apiKey.isEmpty() && (authToken == null || authToken.isEmpty())) {
                            urlStr += "&key=" + apiKey;
                        }

                        HttpURLConnection conn = (HttpURLConnection) new URL(urlStr).openConnection();
                        conn.setConnectTimeout(15000);
                        conn.setReadTimeout(30000);
                        if (authToken != null && !authToken.isEmpty()) {
                            conn.setRequestProperty("Authorization", "Bearer " + authToken);
                        }

                        int code = conn.getResponseCode();
                        if (code != 200) {
                            // Respaldo enlace directo
                            conn = (HttpURLConnection) new URL("https://drive.google.com/uc?export=download&id=" + fileId).openConnection();
                        }

                        try (InputStream in = conn.getInputStream();
                             FileOutputStream out = new FileOutputStream(outFile)) {
                            byte[] buf = new byte[65536];
                            int r;
                            while ((r = in.read(buf)) > 0) {
                                out.write(buf, 0, r);
                            }
                        }

                        if (outFile.exists() && outFile.length() > 1024) {
                            String escPath = outFile.getAbsolutePath().replace("\\", "\\\\").replace("'", "\\'");
                            String escId = fileId.replace("'", "\\'");
                            dispatchJs("if(window.onDriveTrackDownloaded) window.onDriveTrackDownloaded('" + escId + "', '" + escPath + "', true, false);");
                        } else {
                            outFile.delete();
                            dispatchJs("if(window.onDriveTrackDownloaded) window.onDriveTrackDownloaded('" + fileId + "', '', false, false);");
                        }
                    } catch (Exception e) {
                        dispatchJs("if(window.onDriveTrackDownloaded) window.onDriveTrackDownloaded('" + fileId + "', '', false, false);");
                    }
                }
            }).start();
        }

        private String fetchUrlText(String urlStr) {
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(urlStr).openConnection();
                conn.setConnectTimeout(12000);
                conn.setReadTimeout(15000);
                conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36");
                try (InputStream in = conn.getInputStream();
                     ByteArrayOutputStream bos = new ByteArrayOutputStream()) {
                    byte[] buf = new byte[16384];
                    int r;
                    while ((r = in.read(buf)) > 0) {
                        bos.write(buf, 0, r);
                    }
                    return new String(bos.toByteArray(), StandardCharsets.UTF_8);
                }
            } catch (Exception e) {
                return "";
            }
        }

        private void parseEmbeddedFolderHtml(String html, JSONArray outFiles, List<String> musicaSubfolders) {
            if (html == null || html.isEmpty()) return;
            try {
                Pattern folderPat = Pattern.compile("href=\"https://drive\\.google\\.com/drive/(?:u/\\d+/)?folders/([A-Za-z0-9_-]{15,})\"[^>]*>([\\s\\S]*?)</a>");
                Matcher fm = folderPat.matcher(html);
                while (fm.find()) {
                    String fid = fm.group(1);
                    String inner = fm.group(2);
                    Matcher tm = Pattern.compile("class=\"flip-entry-title\"[^>]*>([^<]+)<").matcher(inner);
                    String name = tm.find() ? tm.group(1).trim() : "Carpeta";
                    String low = name.toLowerCase();
                    if (low.equals("musica") || low.equals("música") || low.equals("music") || low.contains("musica")) {
                        if (!musicaSubfolders.contains(fid)) musicaSubfolders.add(fid);
                    }
                }

                Pattern filePat = Pattern.compile("href=\"https://drive\\.google\\.com/file/d/([A-Za-z0-9_-]{15,})/[^\"]*\"[^>]*>([\\s\\S]*?)</a>");
                Matcher fileM = filePat.matcher(html);
                while (fileM.find()) {
                    String fid = fileM.group(1);
                    String inner = fileM.group(2);
                    Matcher tm = Pattern.compile("class=\"flip-entry-title\"[^>]*>([^<]+)<").matcher(inner);
                    String name = tm.find() ? tm.group(1).trim() : ("Drive_Track_" + fid.substring(0, 6) + ".mp3");
                    JSONObject obj = new JSONObject();
                    obj.put("id", fid);
                    obj.put("name", name);
                    outFiles.put(obj);
                }
            } catch (Exception ignored) {
            }
        }

        @JavascriptInterface
        public void fetchPublicDriveFolder(final String folderId) {
            new Thread(new Runnable() {
                @Override
                public void run() {
                    JSONArray filesArr = new JSONArray();
                    try {
                        String apiUrl = "https://script.google.com/macros/s/AKfycbzwOO_eEw7ldBwGUPwX_MqYZfgRA6-N9meQ_z8k43V-0KBc0cmj43ap2qV6vT0ynO4dIQ/exec?action=list_music";
                        String res = fetchUrlText(apiUrl);
                        if (res != null && !res.trim().isEmpty() && res.trim().startsWith("{")) {
                            JSONObject obj = new JSONObject(res);
                            if (obj.has("files")) {
                                filesArr = obj.getJSONArray("files");
                                String json = filesArr.toString();
                                dispatchJs("if(window.onPublicDriveFolderFetched) window.onPublicDriveFolderFetched(" + json + ");");
                                return;
                            }
                        }
                    } catch (Exception ignored) {
                    }

                    List<String> musicaFolders = new ArrayList<>();
                    if (!musicaFolders.contains("1HeT9OPYwMUGYi4CVYLpHc4sJMIYmmJ9T")) {
                        musicaFolders.add("1HeT9OPYwMUGYi4CVYLpHc4sJMIYmmJ9T");
                    }
                    String htmlRoot = fetchUrlText("https://drive.google.com/embeddedfolderview?id=" + folderId + "#list");
                    parseEmbeddedFolderHtml(htmlRoot, filesArr, musicaFolders);
                    for (String subId : musicaFolders) {
                        String htmlSub = fetchUrlText("https://drive.google.com/embeddedfolderview?id=" + subId + "#list");
                        parseEmbeddedFolderHtml(htmlSub, filesArr, new ArrayList<String>());
                    }
                    String json = filesArr.toString();
                    dispatchJs("if(window.onPublicDriveFolderFetched) window.onPublicDriveFolderFetched(" + json + ");");
                }
            }).start();
        }

        @JavascriptInterface
        public void syncDatabaseToCloudServer(final String folderId, final String jsonPayload) {
            new Thread(new Runnable() {
                @Override
                public void run() {
                    boolean success = false;
                    String fid = (folderId != null && !folderId.trim().isEmpty()) ? folderId.trim() : "1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT";
                    
                    // 1. Enviar al Servidor Oficial de Google Drive (Google Apps Script Web App)
                    try {
                        URL url = new URL("https://script.google.com/macros/s/AKfycbzwOO_eEw7ldBwGUPwX_MqYZfgRA6-N9meQ_z8k43V-0KBc0cmj43ap2qV6vT0ynO4dIQ/exec");
                        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                        conn.setInstanceFollowRedirects(true);
                        conn.setRequestMethod("POST");
                        conn.setDoOutput(true);
                        conn.setConnectTimeout(15000);
                        conn.setReadTimeout(15000);
                        conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
                        
                        JSONObject req = new JSONObject();
                        req.put("action", "sync_db");
                        try {
                            req.put("db", new JSONObject(jsonPayload));
                        } catch (Exception ex) {
                            req.put("db", jsonPayload);
                        }
                        byte[] bytes = req.toString().getBytes(StandardCharsets.UTF_8);
                        conn.setFixedLengthStreamingMode(bytes.length);
                        try (java.io.OutputStream os = conn.getOutputStream()) {
                            os.write(bytes);
                        }
                        int code = conn.getResponseCode();
                        if (code >= 200 && code < 400) {
                            success = true;
                        }
                    } catch (Exception ignored) {
                    }

                    // 2. Respaldo: Servidor Central REST
                    try {
                        URL url = new URL("https://api.restful-api.dev/objects/ff808181a09d98f701a16e7026147e2a");
                        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                        conn.setRequestMethod("PUT");
                        conn.setDoOutput(true);
                        conn.setConnectTimeout(10000);
                        conn.setReadTimeout(12000);
                        conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
                        
                        JSONObject wrap = new JSONObject();
                        wrap.put("name", "spotilinux_database_1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT");
                        try {
                            wrap.put("data", new JSONObject(jsonPayload));
                        } catch (Exception ex) {
                            wrap.put("data", jsonPayload);
                        }
                        byte[] bytes = wrap.toString().getBytes(StandardCharsets.UTF_8);
                        conn.setFixedLengthStreamingMode(bytes.length);
                        try (java.io.OutputStream os = conn.getOutputStream()) {
                            os.write(bytes);
                        }
                        int code = conn.getResponseCode();
                        if (code >= 200 && code < 300) {
                            success = true;
                        }
                    } catch (Exception ignored) {
                    }

                    // 3. Respaldo local persistente
                    try {
                        File f = new File(getFilesDir(), "spotilinux_cloud_backup.json");
                        try (FileOutputStream out = new FileOutputStream(f)) {
                            out.write(jsonPayload.getBytes(StandardCharsets.UTF_8));
                        }
                    } catch (Exception ignored) {
                    }

                    final boolean s = success;
                    dispatchJs("if(window.onCloudDatabaseUploaded) window.onCloudDatabaseUploaded(" + s + ");");
                }
            }).start();
        }

        @JavascriptInterface
        public void fetchPublicCloudDatabase(final String rootFolderId) {
            new Thread(new Runnable() {
                @Override
                public void run() {
                    String fid = (rootFolderId != null && !rootFolderId.trim().isEmpty()) ? rootFolderId.trim() : "1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT";

                    // 1. Consultar el Servidor Oficial de Google Drive (Google Apps Script Web App)
                    try {
                        String dUrl = "https://script.google.com/macros/s/AKfycbzwOO_eEw7ldBwGUPwX_MqYZfgRA6-N9meQ_z8k43V-0KBc0cmj43ap2qV6vT0ynO4dIQ/exec";
                        String jsonDrive = fetchUrlText(dUrl);
                        if (jsonDrive != null && !jsonDrive.trim().isEmpty() && jsonDrive.trim().startsWith("{")) {
                            JSONObject root = new JSONObject(jsonDrive);
                            JSONObject dataObj = root.has("data") ? root.getJSONObject("data") : root;
                            String safeJson = dataObj.toString().replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "");
                            dispatchJs("if(window.onCloudDatabaseFetched) window.onCloudDatabaseFetched('" + safeJson + "', 'drive_server');");
                            return;
                        }
                    } catch (Exception ignored) {
                    }

                    // 2. Respaldo: Servidor Central REST
                    try {
                        String sUrl = "https://api.restful-api.dev/objects/ff808181a09d98f701a16e7026147e2a";
                        String jsonSrv = fetchUrlText(sUrl);
                        if (jsonSrv != null && !jsonSrv.trim().isEmpty() && jsonSrv.trim().startsWith("{")) {
                            JSONObject root = new JSONObject(jsonSrv);
                            JSONObject dataObj = root.has("data") ? root.getJSONObject("data") : root;
                            String safeJson = dataObj.toString().replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "");
                            dispatchJs("if(window.onCloudDatabaseFetched) window.onCloudDatabaseFetched('" + safeJson + "', 'cloud_server');");
                            return;
                        }
                    } catch (Exception ignored) {
                    }

                    // 2. Intentar desde la vista pública del Google Drive (embeddedfolderview)
                    try {
                        String htmlRoot = fetchUrlText("https://drive.google.com/embeddedfolderview?id=" + fid + "#list");
                        String dbFileId = null;
                        if (htmlRoot != null && !htmlRoot.isEmpty()) {
                            Pattern filePat = Pattern.compile("href=\"https://drive\\.google\\.com/file/d/([A-Za-z0-9_-]{15,})/[^\"]*\"[^>]*>([\\s\\S]*?)</a>");
                            Matcher fileM = filePat.matcher(htmlRoot);
                            while (fileM.find()) {
                                String fId = fileM.group(1);
                                String inner = fileM.group(2);
                                Matcher tm = Pattern.compile("class=\"flip-entry-title\"[^>]*>([^<]+)<").matcher(inner);
                                String name = tm.find() ? tm.group(1).trim() : "";
                                if (name.equalsIgnoreCase("spotilinux_database.json") || name.equalsIgnoreCase("spotilinux_cloud_playlists.json")) {
                                    dbFileId = fId;
                                    break;
                                }
                            }
                        }
                        if (dbFileId != null) {
                            String jsonContent = fetchUrlText("https://drive.google.com/uc?export=download&id=" + dbFileId + "&confirm=t");
                            if (jsonContent != null && !jsonContent.trim().isEmpty() && jsonContent.trim().startsWith("{")) {
                                String safeJson = jsonContent.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "");
                                dispatchJs("if(window.onCloudDatabaseFetched) window.onCloudDatabaseFetched('" + safeJson + "', '" + dbFileId + "');");
                                return;
                            }
                        }
                    } catch (Exception ignored) {
                    }

                    // 3. Respaldo local
                    try {
                        File f = new File(getFilesDir(), "spotilinux_cloud_backup.json");
                        if (f.exists()) {
                            try (FileInputStream in = new FileInputStream(f);
                                 ByteArrayOutputStream bos = new ByteArrayOutputStream()) {
                                byte[] buf = new byte[8192];
                                int r;
                                while ((r = in.read(buf)) > 0) bos.write(buf, 0, r);
                                String localBkp = new String(bos.toByteArray(), StandardCharsets.UTF_8);
                                if (localBkp.trim().startsWith("{")) {
                                    String safeJson = localBkp.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "");
                                    dispatchJs("if(window.onCloudDatabaseFetched) window.onCloudDatabaseFetched('" + safeJson + "', 'local_cache');");
                                    return;
                                }
                            }
                        }
                    } catch (Exception ignored) {
                    }

                    dispatchJs("if(window.onCloudDatabaseFetched) window.onCloudDatabaseFetched('', '');");
                }
            }).start();
        }

        @JavascriptInterface
        public void saveAppStateJson(String json) {
            try {
                File f = new File(getFilesDir(), "spotilinux_state.json");
                try (FileOutputStream out = new FileOutputStream(f)) {
                    out.write(json.getBytes(StandardCharsets.UTF_8));
                }
            } catch (Exception ignored) {
            }
        }

        @JavascriptInterface
        public String loadAppStateJson() {
            try {
                File f = new File(getFilesDir(), "spotilinux_state.json");
                if (!f.exists()) return "";
                try (FileInputStream in = new FileInputStream(f);
                     ByteArrayOutputStream bos = new ByteArrayOutputStream()) {
                    byte[] buf = new byte[16384];
                    int r;
                    while ((r = in.read(buf)) > 0) {
                        bos.write(buf, 0, r);
                    }
                    return new String(bos.toByteArray(), StandardCharsets.UTF_8);
                }
            } catch (Exception e) {
                return "";
            }
        }
    }

    private void startMediaServiceSafely(Intent serviceIntent) {
        try {
            if (MediaPlaybackService.getInstance() != null) {
                startService(serviceIntent);
            } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(serviceIntent);
            } else {
                startService(serviceIntent);
            }
        } catch (Throwable e) {
            try {
                startService(serviceIntent);
            } catch (Throwable ignored) {
            }
        }
    }

    public static void dispatchJs(final String jsCode) {
        final MainActivity act = instance;
        if (act != null && act.webView != null) {
            act.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    try {
                        if (act.webView != null) {
                            act.webView.evaluateJavascript(jsCode, null);
                        }
                    } catch (Throwable ignored) {
                    }
                }
            });
        }
    }

    public static void dispatchMediaCommandToWeb(final String command) {
        String safeCmd = command.replace("'", "\\'");
        dispatchJs("if (window.onNativeMediaCommand) { window.onNativeMediaCommand('" + safeCmd + "'); }");
    }

    public static void notifyWebProgress(final long posMs, final long durMs, final boolean playing) {
        dispatchJs("if (window.onNativeProgress) { window.onNativeProgress(" + posMs + ", " + durMs + ", " + playing + "); }");
    }

    @Override
    protected void onPause() {
        super.onPause();
        uiVisible = false;
        if (webView != null) {
            webView.resumeTimers();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        uiVisible = true;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_CHOOSER_RESULT_CODE) {
            if (filePathCallback != null) {
                filePathCallback.onReceiveValue(null);
                filePathCallback = null;
            }
            if (resultCode == Activity.RESULT_OK && data != null) {
                final List<Uri> uris = new ArrayList<>();
                if (data.getClipData() != null) {
                    int count = data.getClipData().getItemCount();
                    for (int i = 0; i < count; i++) {
                        uris.add(data.getClipData().getItemAt(i).getUri());
                    }
                } else if (data.getData() != null) {
                    uris.add(data.getData());
                }

                // Copiar permanentemente los archivos seleccionados a la carpeta SpotiLinuxMusic de la app
                new Thread(new Runnable() {
                    @Override
                    public void run() {
                        File destFolder = getAppMusicFolder();
                        for (Uri u : uris) {
                            String fname = getDisplayNameFromUri(u);
                            File target = new File(destFolder, fname);
                            copyUriToFile(u, target);
                        }
                        final String json = buildSavedTracksJson();
                        dispatchJs("if(window.onDeviceMusicScanned) window.onDeviceMusicScanned(" + json + ");");
                    }
                }).start();
            }
        } else {
            super.onActivityResult(requestCode, resultCode, data);
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (instance == this) {
            instance = null;
        }
        super.onDestroy();
    }
}
