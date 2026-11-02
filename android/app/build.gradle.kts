import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// 서명 정보는 android/keystore.properties(커밋 금지)에 둡니다. 없으면 release 는 서명 없이 빌드됩니다.
val keystoreProps = Properties().apply {
    rootProject.file("keystore.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}

// 개인 빌드용 기본 서버 설정: android/secrets.properties (git 에 올리지 않음)
//   serverUrl=http://...   apiKey=...
// 파일이 없으면 빈 값 → 앱에서 직접 입력 (공개 APK)
val secretProps = Properties().apply {
    rootProject.file("secrets.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}
fun secret(name: String): String = secretProps.getProperty(name, "").trim().replace("\"", "").replace("\\", "")

android {
    namespace = "com.mahal.kakaocollector"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.mahal.kakaocollector"
        minSdk = 26
        targetSdk = 34
        versionCode = 18
        versionName = "2.2.2"
        buildConfigField("String", "DEFAULT_SERVER_URL", "\"${secret("serverUrl")}\"")
        buildConfigField("String", "DEFAULT_API_KEY", "\"${secret("apiKey")}\"")
    }

    signingConfigs {
        if (keystoreProps.isNotEmpty()) {
            create("release") {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        buildConfig = true
    }
    testOptions {
        unitTests.isIncludeAndroidResources = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.13")
    testImplementation("androidx.test:core:1.6.1")
}
