pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "KakaoCollector"
include(":app")
include(":local") // 카톡 메시지 보관함 (서버 없이 휴대폰에만 저장)
include(":chatbot") // 카톡 챗봇: 수집 앱과 같고 알림을 지우지 않음 (갤럭시 S10+ · Android 12 용)
