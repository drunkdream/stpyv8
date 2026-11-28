#include "libplatform/libplatform.h"

#include "Platform.h"

std::unique_ptr<v8::Platform> CPlatform::platform;
bool CPlatform::inited = false;

void CPlatform::Init()
{
    if(inited)
        return;

    std::string icu_data_file = GetICUDataFile();

    v8::V8::InitializeICUDefaultLocation(
        argv.c_str(), icu_data_file.empty() ? nullptr : icu_data_file.c_str());
    v8::V8::InitializeExternalStartupData(argv.c_str());

    platform = v8::platform::NewDefaultPlatform();

    v8::V8::InitializePlatform(platform.get());
    v8::V8::Initialize();

    inited = true;
}

std::string CPlatform::GetICUDataFile()
{
#if defined(_WIN32) || defined (_WIN64)
    boost::filesystem::path icu_data_path = getenv("APPDATA");
#else
    boost::filesystem::path icu_data_path = getenv("HOME");
#endif
    if (icu_data_user == nullptr)
        return std::string();

    if (boost::filesystem::is_directory(icu_data_path)) {
        icu_data_path /= icu_data_user;

        std::ifstream ifile(icu_data_path.string().c_str());
        if (ifile.good())
            return icu_data_path.string();
    }

    if (icu_data_system != nullptr) {
#if defined(_WIN32) || defined (_WIN64)
        boost::filesystem::path icu_windows_data_path = getenv("PROGRAMDATA");
        if (boost::filesystem::is_directory(icu_windows_data_path)) {
            icu_windows_data_path /= icu_data_system;

            std::ifstream ifile(icu_windows_data_path.string().c_str());
            if (ifile.good())
                return icu_windows_data_path.string();
        }
#else
        std::ifstream ifile(icu_data_system);
        if (ifile.good())
            return std::string(icu_data_system);
#endif
    }

    return std::string();
}
