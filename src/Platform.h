#pragma once

#include <fstream>
#include <boost/filesystem.hpp>
#include <v8.h>

#include "Config.h"


class CPlatform
{
private:
    static bool inited;
    static std::unique_ptr<v8::Platform> platform;

    constexpr static const char *icu_data_system = ICU_DATA_SYSTEM;
    constexpr static const char *icu_data_user = ICU_DATA_USER;

    // Returns the path as a std::string: returning a `const char*` obtained from
    // a local std::string would dangle as soon as the function returns.
    std::string GetICUDataFile();

    std::string argv;
public:
    CPlatform() : argv(std::string()) {};
    CPlatform(std::string argv0) : argv(argv0) {};
    ~CPlatform() {};
    void Init();
};
